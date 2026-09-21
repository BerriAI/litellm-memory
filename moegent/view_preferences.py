"""Personal retrieval filters. These narrow existing permissions, never grant access."""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def source_key(value):
    value = str(value).strip().casefold()
    return {'claude_code': 'claude', 'claude code': 'claude',
            'added manually': 'manual', 'imported': 'legacy'}.get(value, value)


def projects_in(record):
    values = list(record.get('projects') or [])
    metadata = record.get('metadata') or {}
    for name in ['project', 'projects']:
        value = metadata.get(name)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(v for v in value if isinstance(v, str))
    return {value.strip().casefold() for value in values if isinstance(value, str)}


def types_in(record):
    labels = record.get('labels') or {}
    if labels:
        return {name.casefold() for name, confidence in labels.items()
                if isinstance(confidence, (int, float)) and confidence >= 0.8}
    # Unprocessed and historical sources retain their original metadata type.
    return {str(record.get('kind', '')).casefold()}


class ViewPreferences(BaseModel):
    hidden_people: list[Name] = Field(default_factory=list, max_length=200)
    hidden_projects: list[Name] = Field(default_factory=list, max_length=100)
    hidden_sources: list[Name] = Field(default_factory=list, max_length=100)
    hidden_types: list[Name] = Field(default_factory=list, max_length=100)

    @field_validator('hidden_people', 'hidden_projects', 'hidden_sources', 'hidden_types')
    @classmethod
    def normalize(cls, values, info):
        if info.field_name == 'hidden_sources':
            values = [source_key(value) for value in values]
        elif info.field_name != 'hidden_people':
            values = [value.casefold() for value in values]
        return sorted(set(values))

    @property
    def active(self):
        return any(self.model_dump().values())

    def includes(self, record):
        return not (record.get('owner') in self.hidden_people
                    or source_key(record.get('source', '')) in self.hidden_sources
                    or types_in(record).intersection(self.hidden_types)
                    or projects_in(record).intersection(self.hidden_projects))
