"""Private, resumable evaluation of the actual query API; no automatic LLM judge."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import httpx
from nacl.exceptions import CryptoError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .connector import LocalCapture, atomic_private, capture_directory
from .crypto import encode

CATEGORIES = (
    'exact_id', 'paraphrase', 'changed_decision', 'unknown_date', 'incomplete_history',
    'conflicting_perspectives', 'revoked', 'private', 'unsupported',
)


class Case(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    category: Literal[*CATEGORIES]
    question: str = Field(min_length=1, max_length=16000)
    rubric: str = Field(min_length=1, max_length=16000)
    expectation: Literal['answer', 'abstain']
    audience: Literal['owner', 'public'] = 'owner'
    scope: str | None = Field(default=None, max_length=200)
    required_citations: list[str] = Field(default_factory=list, max_length=100)
    forbidden_citations: list[str] = Field(default_factory=list, max_length=100)
    forbidden_text: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def evidence(self):
        if not self.question.strip() or not self.rubric.strip():
            raise ValueError('Each case needs a question and reviewed evidence rubric.')
        if self.expectation == 'answer' and not self.required_citations:
            raise ValueError('Answerable cases need expected evidence IDs.')
        for value in self.required_citations + self.forbidden_citations:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value):
                raise ValueError('Evidence IDs must be literal record IDs.')
        if set(self.required_citations) & set(self.forbidden_citations):
            raise ValueError('Evidence cannot be both required and forbidden.')
        if any(not value.strip() or len(value) > 16000 for value in self.forbidden_text):
            raise ValueError('Privacy canaries must be nonempty and bounded.')
        if self.category in {'revoked', 'private'} and not (self.forbidden_citations or self.forbidden_text):
            raise ValueError('Privacy cases need forbidden evidence or a text canary.')
        return self


class Dataset(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    id: UUID
    corpus: Literal['real_history', 'synthetic']
    owner: str = Field(min_length=3, max_length=200)
    deployment_revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    cases: list[Case] = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def unique(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError('Case IDs must be unique.')
        questions = {(' '.join(c.question.casefold().split()), c.audience, c.scope) for c in self.cases}
        if len(questions) != len(self.cases):
            raise ValueError('Duplicate questions do not increase evaluation coverage.')
        return self


def fingerprint(value):
    return hashlib.sha256(encode(value)).hexdigest()


def outside_repository(path):
    resolved = path.expanduser().resolve()
    if any((parent / '.git').exists() for parent in (resolved, *resolved.parents)):
        raise ValueError('Keep evaluation inputs and state outside every Git checkout.')
    return resolved


def read_dataset(path):
    path = outside_repository(path)
    if path.stat().st_mode & 0o077:
        raise ValueError('Make the private question file readable only by you (chmod 600).')
    with path.open('rb') as file:
        raw = file.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError('The question file is too large.')
    return Dataset.model_validate_json(raw)


def create_run(dataset, connection, model):
    if connection['owner'] != dataset.owner:
        raise ValueError('The question set belongs to a different Slack identity.')
    if not model or len(model) > 200:
        raise ValueError('Select an available LiteLLM chat model.')
    return {
        'version': 1, 'id': str(uuid4()), 'dataset': dataset.model_dump(mode='json'),
        'url': connection['url'].rstrip('/'), 'model': model,
        'created_at': datetime.now(UTC).isoformat(),
        'cases': {str(case.id): {'request_id': str(uuid4()), 'status': 'pending'} for case in dataset.cases},
    }


def check_answer(case, answer, base_url):
    cited, invalid = set(), 0
    base = urlsplit(base_url)
    # Match the application's citation convention; semantic grounding still
    # requires reading the evidence. A valid ID alone never establishes truth.
    for target in re.findall(r'\]\(([^\s)]+)\)', answer):
        try:
            url = urlsplit(target)
        except ValueError:
            invalid += 1
            continue
        if '/memory/' not in unquote(url.path):
            continue
        record_id = unquote(url.path).removeprefix('/memory/')
        cited.add(record_id)
        if (url.scheme != base.scheme or url.netloc != base.netloc or url.query or url.fragment
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', record_id)):
            invalid += 1
    folded = answer.casefold()
    leaks = sum(value.casefold() in folded for value in case.forbidden_text)
    # Scan the entire answer too: an exposed ID is a failure even without a link.
    leaks += sum(value in answer or value in cited for value in case.forbidden_citations)
    return {'missing_citations': len(set(case.required_citations) - cited),
            'invalid_citations': invalid, 'privacy_failures': leaks}


class Evaluation:
    def __init__(self, local, *, transport=None, poll_seconds=5, timeout=600):
        self.local = local
        self.transport = transport
        self.poll_seconds = poll_seconds
        self.timeout = timeout

    def connection(self):
        self.local.flush(limit=0, transport=self.transport)
        connection = self.local.get('connection')
        if not connection or connection.get('expires_at', 0) <= time.time() + 30:
            raise ValueError('Reconnect litellm-memory-connector before running this evaluation.')
        url = urlsplit(connection['url'])
        if (url.username or url.password or url.query or url.fragment or url.path not in {'', '/'}
                or url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'127.0.0.1', 'localhost'})):
            raise ValueError('The connection must use HTTPS or a local loopback installation.')
        return connection

    def request(self, method, path, run=None, **kwargs):
        connection = self.connection()
        if run and (run['url'] != connection['url'].rstrip('/') or run['dataset']['owner'] != connection['owner']):
            raise ValueError('Reconnect the same installation and Slack identity to resume this run.')
        with httpx.Client(base_url=connection['url'], transport=self.transport, timeout=30,
                          follow_redirects=False, headers={'Authorization': 'Bearer ' + connection['access_token']}) as client:
            return client.request(method, path, **kwargs)

    def path(self, identity):
        return self.local.directory / 'evaluations' / (str(UUID(str(identity))) + '.encrypted')

    @contextlib.contextmanager
    def lock(self):
        path = self.local.directory / 'evaluations.lock'
        with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR, 0o600), 'w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another evaluation command is using this connection directory.') from None
            yield

    def save(self, run):
        atomic_private(self.path(run['id']), self.local.vault.seal_operational(run))

    def load(self, identity):
        run = self.local.vault.open_operational(self.path(identity).read_text())
        if run['version'] != 1 or run['id'] != str(UUID(str(identity))):
            raise ValueError('Unrecognized evaluation run.')
        Dataset.model_validate(run['dataset'])
        return run

    def start(self, dataset, model=None):
        connection = self.connection()
        # Check ownership before making a model request or storing private inputs.
        if dataset.owner != connection['owner']:
            raise ValueError('The question set belongs to a different Slack identity.')
        response = self.request('GET', '/api/v1/models')
        response.raise_for_status()
        catalog = response.json()
        chosen = model or catalog['default']
        if chosen not in {entry['id'] for entry in catalog['data']}:
            raise ValueError('The selected chat model is not available through this connection.')
        run = create_run(dataset, connection, chosen)
        self.save(run)  # Persist every idempotency key before the first submission.
        return run

    def execute(self, run):
        if run.get('cancel_requested'):
            self.cancel(run)
            return
        dataset = Dataset.model_validate(run['dataset'])
        for case in dataset.cases:
            entry = run['cases'][str(case.id)]
            if entry['status'] in {'done', 'failed', 'cancelled'}:
                continue
            deadline = time.monotonic() + self.timeout
            if not entry.get('job_id'):
                self.submit(run, case, entry)
            while True:
                response = self.request('GET', '/api/v1/jobs/' + entry['job_id'], run)
                response.raise_for_status()
                job = response.json()
                if job['id'] != entry['job_id'] or job['status'] not in {
                    'queued', 'running', 'done', 'failed', 'cancelled',
                }:
                    raise ValueError('Unexpected query response; the saved run remains resumable.')
                # Check partial output for privacy leaks too, including a failed
                # answer. Never retain or print raw progress/error messages.
                delivered = '\n'.join(str(job.get(key) or '') for key in ['partial', 'phase', 'error', 'answer'])
                checks = check_answer(case, delivered, run['url'])
                entry['stream_privacy_failures'] = max(entry.get('stream_privacy_failures', 0), checks['privacy_failures'])
                entry['status'] = job['status']
                if job['status'] == 'done':
                    answer = job.get('answer') or ''
                    entry.update(answer=answer, answer_sha256=fingerprint(answer),
                                 checks=check_answer(case, answer, run['url']),
                                 finished_at=datetime.now(UTC).isoformat())
                self.save(run)
                if job['status'] in {'done', 'failed', 'cancelled'}:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('Query is still pending. Resume the saved run or cancel it explicitly.')
                time.sleep(self.poll_seconds)

    def submit(self, run, case, entry):
        entry['status'] = 'submitting'
        self.save(run)
        response = self.request('POST', '/api/v1/chat', run, json={
            'question': case.question, 'audience': case.audience, 'scope': case.scope,
            'model': run['model'], 'request_id': entry['request_id'],
        })
        response.raise_for_status()
        entry.update(job_id=str(UUID(response.json()['id'])), status='queued')
        self.save(run)

    def cancel(self, run):
        run['cancel_requested'] = True
        self.save(run)
        for case in Dataset.model_validate(run['dataset']).cases:
            entry = run['cases'][str(case.id)]
            if entry['status'] in {'done', 'failed', 'cancelled'}:
                continue
            if not entry.get('job_id') and entry['status'] == 'submitting':
                # Resolve only an uncertain *existing* submission with its
                # saved idempotency key. Never submit an unstarted case.
                self.submit(run, case, entry)
            if entry.get('job_id'):
                response = self.request('POST', '/api/v1/jobs/' + entry['job_id'] + '/cancel', run)
                response.raise_for_status()
            entry['status'] = 'cancelled'
            self.save(run)


def summarize(run):
    dataset = Dataset.model_validate(run['dataset'])
    categories = {category: 0 for category in CATEGORIES}
    totals = {kind: {'total': 0, 'reviewed': 0, 'passed': 0} for kind in ['answer', 'abstain']}
    privacy, completed, infrastructure_failures = 0, 0, 0
    for case in dataset.cases:
        categories[case.category] += 1
        entry = run['cases'][str(case.id)]
        group = totals[case.expectation]
        group['total'] += 1
        completed += entry['status'] == 'done'
        infrastructure_failures += entry['status'] in {'failed', 'cancelled'}
        checks = entry.get('checks', {})
        privacy += checks.get('privacy_failures', 0) + entry.get('stream_privacy_failures', 0)
        review = entry.get('review')
        if (entry['status'] == 'done' and review and entry.get('answer_sha256') == fingerprint(entry['answer'])
                and review.get('answer_sha256') == entry['answer_sha256']):
            group['reviewed'] += 1
            privacy += not review['privacy_ok']
            group['passed'] += bool(review['passed'] and review['privacy_ok'] and not any(checks.values())
                                    and not entry.get('stream_privacy_failures'))
    reasons = []
    if dataset.corpus != 'real_history':
        reasons.append('synthetic_corpus')
    if len(dataset.cases) < 100 or min(categories.values()) < 5 or min(g['total'] for g in totals.values()) < 10:
        reasons.append('insufficient_coverage')
    if not any(c.audience == 'public' and c.category == 'private' for c in dataset.cases):
        reasons.append('missing_shared_audience_privacy_case')
    if completed != len(dataset.cases):
        reasons.append('incomplete_run')
    if any(group['reviewed'] != group['total'] for group in totals.values()):
        reasons.append('unreviewed_answers')
    if privacy:
        reasons.append('privacy_failure')
    for kind, threshold in [('answer', .90), ('abstain', .95)]:
        group = totals[kind]
        group['rate'] = group['passed'] / group['total'] if group['total'] else None
        if group['rate'] is None or group['rate'] < threshold:
            reasons.append(kind + '_below_target')
    return {'run_id': run['id'], 'dataset_id': str(dataset.id), 'corpus': dataset.corpus,
            'declared_deployment_revision': dataset.deployment_revision, 'model': run['model'],
            'cases': len(dataset.cases), 'completed': completed, 'infrastructure_failures': infrastructure_failures,
            'categories': categories, 'quality': totals, 'privacy_failures': privacy,
            'question_quality_gate': 'pass' if not reasons else 'not_passed', 'reasons': reasons,
            'scope': 'Reviewed API answers only; not proof of corpus authenticity, deployment, Slack or summary quality.'}


def review_answers(evaluation, run, *, ask=input, display=print):
    """The only command that deliberately reveals private text, to its owner."""
    def show(value):
        # Work text is untrusted even in a terminal: strip ANSI/OSC and bidi
        # controls without flattening ordinary paragraphs or tabs.
        display(''.join(f'[U+{ord(char):04X}]' if char not in '\n\t' and
                        unicodedata.category(char) in {'Cc', 'Cf'} else char for char in value))

    for case in Dataset.model_validate(run['dataset']).cases:
        entry = run['cases'][str(case.id)]
        if entry['status'] != 'done':
            continue
        current_hash = fingerprint(entry['answer'])
        if entry.get('review', {}).get('answer_sha256') == current_hash:
            continue
        entry['answer_sha256'] = current_hash
        entry['checks'] = check_answer(case, entry['answer'], run['url'])
        show(f'\nCase {case.id} ({case.category}, {case.audience})')
        show('Question: ' + case.question)
        show('Evidence rubric: ' + case.rubric)
        show('Required evidence: ' + ', '.join(case.required_citations))
        show('Answer:\n' + entry['answer'])
        show('Automated checks: ' + json.dumps(entry['checks']))
        standard = ('correct, fully supported by the cited originals, with faithful dates and attribution'
                    if case.expectation == 'answer' else 'appropriately qualified or abstaining from unsupported claims')
        verdict = ask(f'After reading the originals, is this answer {standard}? [y/n/s/q] ').strip().lower()
        if verdict == 'q':
            break
        if verdict not in {'y', 'n'}:
            continue
        privacy = ask('Is every disclosed fact and citation allowed for this audience? [y/n/s] ').strip().lower()
        if privacy not in {'y', 'n'}:
            continue
        entry['review'] = {'answer_sha256': entry['answer_sha256'], 'passed': verdict == 'y',
                           'privacy_ok': privacy == 'y', 'reviewed_at': datetime.now(UTC).isoformat()}
        evaluation.save(run)


def main():
    parser = argparse.ArgumentParser(description='Privately evaluate real work-history answers through LiteLLM Memory.')
    parser.add_argument('--directory', type=Path, default=capture_directory())
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('start', help='Submit explicit questions; preserves encrypted, resumable results.')
    start.add_argument('questions', type=Path)
    start.add_argument('--model', help='An available LiteLLM chat model; defaults to the configured agent model.')
    for command in ['resume', 'report', 'review', 'cancel']:
        child = sub.add_parser(command, help='Review prints private text; other commands print only aggregate results.')
        child.add_argument('run_id', type=UUID)
    args = parser.parse_args()
    local = None
    try:
        directory = outside_repository(args.directory)
        if not (directory / 'capture.sqlite3').is_file():
            raise ValueError('Connect with litellm-memory-connector first.')
        local = LocalCapture(directory)
        evaluation = Evaluation(local)
        with evaluation.lock():
            if args.command == 'start':
                run = evaluation.start(read_dataset(args.questions), args.model)
                print('Run ' + run['id'], flush=True)
            else:
                run = evaluation.load(args.run_id)
            if args.command in {'start', 'resume'}:
                evaluation.execute(run)
            elif args.command == 'review':
                review_answers(evaluation, run)
            elif args.command == 'cancel':
                evaluation.cancel(run)
            report = summarize(run)
            print(json.dumps(report, indent=2))
            if args.command == 'report' and report['question_quality_gate'] != 'pass':
                raise SystemExit(2)
    except (ValueError, KeyError, OSError, CryptoError, httpx.HTTPError, TimeoutError):
        # Validation/upstream exception strings can include private inputs. Never
        # print them, response bodies, credentials, or raw query output here.
        print('Evaluation stopped. Check private inputs, connection and access; resume the saved run after fixing it.')
        raise SystemExit(1) from None
    except (KeyboardInterrupt, EOFError):
        print('\nStopped. Saved requests and reviews remain resumable; use cancel to stop a known pending job.')
        raise SystemExit(130) from None
    finally:
        if local:
            local.close()


if __name__ == '__main__':
    main()
