import copy
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_model_settings import application, enroll

from litellm_memory.connector import LocalCapture
from litellm_memory.evaluation import (
    CATEGORIES,
    Case,
    Dataset,
    Evaluation,
    check_answer,
    create_run,
    fingerprint,
    main,
    outside_repository,
    read_dataset,
    review_answers,
    summarize,
)


def question(**changes):
    return Case(id=uuid4(), category='exact_id', question='What happened to artifact EVAL-42?',
                rubric='The original records a proposal, not a completed release.', expectation='answer',
                required_citations=['a' * 64], **changes)


def dataset(cases=None):
    return Dataset(id=uuid4(), corpus='synthetic', owner='T:OWNER', deployment_revision='d' * 40,
                   cases=cases or [question()])


@pytest.fixture
def local(tmp_path):
    connection = LocalCapture(tmp_path / 'private-state')
    connection.set('connection', {'url': 'https://memory.example', 'owner': 'T:OWNER',
                                  'access_token': 'test-token', 'expires_at': time.time() + 3600})
    connection.db.commit()
    try:
        yield connection
    finally:
        connection.close()


def answered_run(local, data=None):
    run = create_run(data or dataset(), local.get('connection'), 'gateway-chat')
    for entry in run['cases'].values():
        answer = 'It is a proposal. [Source](https://memory.example/memory/' + 'a' * 64 + ')'
        entry.update(status='done', answer=answer, answer_sha256=fingerprint(answer),
                     checks={'missing_citations': 0, 'invalid_citations': 0, 'privacy_failures': 0})
    return run


def test_question_inputs_reject_duplicate_or_unverifiable_goldens_and_git_storage(tmp_path):
    case = question()
    for changes in [{'required_citations': []}, {'forbidden_citations': case.required_citations},
                    {'question': '  '}, {'forbidden_text': ['']}, {'required_citations': ['../private']}]:
        with pytest.raises(ValueError):
            Case.model_validate(case.model_dump() | changes)
    with pytest.raises(ValueError):
        dataset([case, case.model_copy(update={'id': uuid4()})])
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    (checkout / '.git').write_text('gitdir: /some/worktree')
    with pytest.raises(ValueError):
        outside_repository(checkout / 'private.json')
    shortcut = tmp_path / 'shortcut'
    shortcut.symlink_to(checkout, target_is_directory=True)
    with pytest.raises(ValueError):
        outside_repository(shortcut / 'private.json')
    private = tmp_path / 'questions.json'
    private.write_text(dataset().model_dump_json())
    private.chmod(0o644)
    with pytest.raises(ValueError):
        read_dataset(private)
    private.chmod(0o600)
    assert read_dataset(private).corpus == 'synthetic'


def test_actual_query_api_lost_response_resumes_without_duplicate_and_encrypts_results(local, tmp_path):
    app = application(tmp_path / 'app', [])
    lost = [True]
    with TestClient(app) as api:
        enroll(api, app)
        token = api.portal.call(app.state.archive.create_token, 'T:OWNER', 'evaluation test')['token']
        connection = local.get('connection') | {'access_token': token}
        local.set('connection', connection)
        local.db.commit()
        async def finish(job_id):
            await app.state.store.result(job_id, answer='PRIVATE_ANSWER_CANARY')
        def forward(request):
            result = api.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
            if request.url.path == '/api/v1/chat' and result.is_success:
                api.portal.call(finish, result.json()['id'])
                if lost and lost.pop():
                    raise httpx.ReadError('PRIVATE_HTTP_CANARY', request=request)
            return httpx.Response(result.status_code, json=result.json())
        transport = httpx.MockTransport(forward)
        evaluator = Evaluation(local, transport=transport, poll_seconds=0)
        run = evaluator.start(dataset())
        with pytest.raises(httpx.ReadError):
            evaluator.execute(run)
        persisted = evaluator.load(run['id'])
        assert next(iter(persisted['cases'].values()))['status'] == 'submitting'
        # A new runner uses the same encrypted journal and request ID.
        restarted = Evaluation(local, transport=transport, poll_seconds=0)
        restarted.execute(persisted)
        restarted.execute(persisted)
        async def count():
            return (await (await app.state.store.db.execute("SELECT count(*) FROM jobs WHERE kind='web'")).fetchone())[0]
        assert api.portal.call(count) == 1
        assert next(iter(persisted['cases'].values()))['status'] == 'done'
        ciphertext = evaluator.path(run['id']).read_text()
        assert 'artifact EVAL-42' not in ciphertext and token not in ciphertext
        assert 'PRIVATE_ANSWER_CANARY' not in ciphertext
        assert evaluator.path(run['id']).stat().st_mode & 0o077 == 0
        report = json.dumps(summarize(persisted))
        assert 'artifact EVAL-42' not in report and 'T:OWNER' not in report and token not in report
        assert 'PRIVATE_ANSWER_CANARY' not in report
        assert summarize(persisted)['question_quality_gate'] == 'not_passed'


def test_canaries_cover_citations_partial_and_failed_output_without_recording_error(local):
    case = question(forbidden_text=['PRIVATE_CANARY'], forbidden_citations=['b' * 64])
    assert check_answer(case, 'private_canary', 'https://memory.example')['privacy_failures'] == 1
    assert check_answer(case, 'See ' + 'b' * 64, 'https://memory.example')['privacy_failures'] == 1
    wrong = 'Claim. [Source](https://wrong.example/memory/' + 'a' * 64 + ')'
    assert check_answer(case, wrong, 'https://memory.example')['invalid_citations'] == 1
    run = create_run(dataset([case]), local.get('connection'), 'gateway-chat')
    job_id = str(uuid4())
    responses = [httpx.Response(202, json={'id': job_id}),
                 httpx.Response(200, json={'id': job_id, 'status': 'running', 'partial': 'PRIVATE_CANARY'}),
                 httpx.Response(200, json={'id': job_id, 'status': 'failed', 'error': 'PRIVATE_ERROR'})]
    evaluator = Evaluation(local, transport=httpx.MockTransport(lambda _: responses.pop(0)), poll_seconds=0)
    evaluator.execute(run)
    entry = run['cases'][str(case.id)]
    assert entry['status'] == 'failed' and entry['stream_privacy_failures'] == 1
    assert 'PRIVATE_ERROR' not in json.dumps(run)
    report = summarize(run)
    assert 'privacy_failure' in report['reasons'] and report['infrastructure_failures'] == 1


def test_429_and_cancel_reconcile_only_the_uncertain_submission(local):
    cases = [question(), question().model_copy(update={'question': 'A second, unstarted question?'})]
    run = create_run(dataset(cases), local.get('connection'), 'gateway-chat')
    evaluator = Evaluation(local, transport=httpx.MockTransport(lambda _: httpx.Response(429)), poll_seconds=0)
    with pytest.raises(httpx.HTTPStatusError):
        evaluator.execute(run)
    assert [e['status'] for e in run['cases'].values()] == ['submitting', 'pending']
    paths, ids = [], []
    job_id = str(uuid4())
    def available(request):
        paths.append(request.url.path)
        if request.url.path == '/api/v1/chat':
            ids.append(json.loads(request.content)['request_id'])
            return httpx.Response(202, json={'id': job_id})
        return httpx.Response(200, json={'status': 'cancelled'})
    evaluator.transport = httpx.MockTransport(available)
    evaluator.cancel(run)
    evaluator.execute(run)  # Resume after cancellation cannot start the next case.
    assert paths == ['/api/v1/chat', '/api/v1/jobs/' + job_id + '/cancel']
    assert ids == [run['cases'][str(cases[0].id)]['request_id']]
    assert all(entry['status'] == 'cancelled' for entry in run['cases'].values())


def test_timeout_resumes_existing_job_and_permission_change_never_restarts_it(local):
    run = create_run(dataset(), local.get('connection'), 'gateway-chat')
    job_id, calls = str(uuid4()), []
    def pending(request):
        calls.append(request.method)
        return httpx.Response(202, json={'id': job_id}) if request.method == 'POST' else httpx.Response(
            200, json={'id': job_id, 'status': 'running'})
    evaluator = Evaluation(local, transport=httpx.MockTransport(pending), poll_seconds=0, timeout=0)
    with pytest.raises(TimeoutError):
        evaluator.execute(run)
    assert calls == ['POST', 'GET']
    def revoked(request):
        calls.append(request.method)
        return httpx.Response(409, json={'detail': 'Access changed'})
    evaluator.transport = httpx.MockTransport(revoked)
    with pytest.raises(httpx.HTTPStatusError):
        evaluator.execute(evaluator.load(run['id']))
    assert calls == ['POST', 'GET', 'GET']


def test_account_or_installation_change_cannot_send_private_questions(local):
    run = create_run(dataset(), local.get('connection'), 'gateway-chat')
    evaluator = Evaluation(local, transport=httpx.MockTransport(lambda _: pytest.fail('Must not make a request')))
    original = local.get('connection')
    for change in [{'owner': 'T:OTHER'}, {'url': 'https://different.example'}]:
        local.set('connection', original | change)
        local.db.commit()
        with pytest.raises(ValueError):
            evaluator.execute(copy.deepcopy(run))


def test_paused_capture_allows_read_only_token_refresh_without_uploading(local):
    local.set('paused', True)
    local.set('connection', local.get('connection') | {'expires_at': 0, 'refresh_token': 'r', 'client_id': 'c'})
    local.enqueue({'source_id': 'queued', 'content': 'MUST_NOT_UPLOAD'})
    local.db.commit()
    paths = []
    def refresh(request):
        paths.append(request.url.path)
        assert request.url.path == '/oauth/token'
        assert b'MUST_NOT_UPLOAD' not in request.content
        return httpx.Response(200, json={'access_token': 'new', 'refresh_token': 'next', 'expires_in': 3600})
    transport = httpx.MockTransport(refresh)
    local.flush(transport=transport)
    assert paths == []
    assert Evaluation(local, transport=transport).connection()['access_token'] == 'new'
    assert paths == ['/oauth/token']
    assert local.status()['paused'] and local.status()['queued'] == 1


def test_reviews_bound_to_answers_and_no_synthetic_or_unreviewed_quality_claims(local):
    cases = []
    for index in range(108):
        category = CATEGORIES[index % len(CATEGORIES)]
        cases.append(Case(id=uuid4(), category=category, question=f'Synthetic case {index}?',
                          rubric='Read the original, distinguish a proposal from completion.',
                          audience='public' if category == 'private' else 'owner',
                          expectation='abstain' if category == 'unsupported' else 'answer',
                          required_citations=[] if category == 'unsupported' else ['a' * 64],
                          forbidden_text=['PRIVATE_CANARY'] if category in {'private', 'revoked'} else []))
    run = answered_run(local, dataset(cases))
    assert 'unreviewed_answers' in summarize(run)['reasons']
    for entry in run['cases'].values():
        entry['review'] = {'answer_sha256': entry['answer_sha256'], 'passed': True, 'privacy_ok': True}
    assert summarize(run)['reasons'] == ['synthetic_corpus']
    # Only tests of the scoring logic label the fixture real; no live-history score is claimed.
    run['dataset']['corpus'] = 'real_history'
    assert summarize(run)['question_quality_gate'] == 'pass'
    first = next(iter(run['cases'].values()))
    first['answer'] = 'A changed answer must be reviewed again.'
    assert 'unreviewed_answers' in summarize(run)['reasons']
    first['answer_sha256'] = fingerprint(first['answer'])
    assert 'unreviewed_answers' in summarize(run)['reasons']
    first['review']['answer_sha256'] = first['answer_sha256']
    first['checks']['privacy_failures'] = 1
    assert 'privacy_failure' in summarize(run)['reasons']


def test_interactive_review_preserves_skips_and_persists_explicit_judgments(local):
    evaluator, run = Evaluation(local), answered_run(local)
    messages, replies = [], iter(['s'])
    review_answers(evaluator, run, ask=lambda _: next(replies), display=messages.append)
    assert 'unreviewed_answers' in summarize(run)['reasons']
    replies = iter(['y', 'n'])
    review_answers(evaluator, run, ask=lambda _: next(replies), display=messages.append)
    report = summarize(evaluator.load(run['id']))
    assert report['quality']['answer']['reviewed'] == 1 and report['quality']['answer']['passed'] == 0
    assert 'privacy_failure' in report['reasons']
    assert 'artifact EVAL-42' in '\n'.join(messages)  # Only explicit review prints content.
    entry = next(iter(run['cases'].values()))
    entry['answer'] += '\x1b]52;c;clipboard\x07\u202e'
    replies = iter(['y', 'y'])
    messages.clear()
    review_answers(evaluator, run, ask=lambda _: next(replies), display=messages.append)
    assert '\x1b' not in '\n'.join(messages) and '\u202e' not in '\n'.join(messages)
    assert entry['review']['answer_sha256'] == fingerprint(entry['answer'])


def test_cli_invalid_private_input_does_not_echo_validation_context(local, tmp_path, monkeypatch, capsys):
    path = tmp_path / 'private.json'
    path.write_text('{"PRIVATE_QUESTION_CANARY": "TOKEN_CANARY"}')
    path.chmod(0o600)
    monkeypatch.setattr('sys.argv', ['litellm-memory-evaluate', '--directory', str(local.directory), 'start', str(path)])
    with pytest.raises(SystemExit) as stopped:
        main()
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert 'PRIVATE_QUESTION_CANARY' not in output.out + output.err
    assert 'TOKEN_CANARY' not in output.out + output.err


def test_evaluation_lock_prevents_competing_journal_writers(local):
    with Evaluation(local).lock(), pytest.raises(ValueError):
        with Evaluation(local).lock():
            pass
    assert Path(local.directory / 'evaluations.lock').stat().st_mode & 0o077 == 0
