"""Encrypted, versioned settings for all model roles; no provider credentials in the UI."""

import asyncio
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_model: str = Field(default="", max_length=200)
    embedding_model: str = Field(default="", max_length=200)
    slack_model: str = Field(default="", max_length=200)
    background_model: str = Field(default="", max_length=200)
    classifier_model: str = Field(default="jev-latest", min_length=1, max_length=200)

    @field_validator("agent_model", "embedding_model", "slack_model", "background_model", "classifier_model")
    @classmethod
    def valid_identifier(cls, value):
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Choose a model identifier without surrounding spaces or control characters.")
        return value


class ModelSettingsService:
    # Stable storage key: changing it would discard saved model choices on upgrade.
    key = "litellm-memory:model-configuration"

    def __init__(self, settings, store, gateway, archive):
        self.settings, self.store, self.gateway, self.archive = settings, store, gateway, archive
        self.lock = asyncio.Lock()
        self.configuration = None
        self.revision = 0
        self.last_checks = {}

    async def load(self):
        raw = await self.store.setting(self.key)
        if raw:
            saved = json.loads(raw)
            self.configuration = ModelConfiguration.model_validate(saved["models"])
            self.revision = saved["revision"]
            self.last_checks = saved.get("checks", {})
            self.apply()

    def apply(self):
        c = self.configuration
        self.settings.litellm_model = c.agent_model
        self.settings.memory_embedding_model = c.embedding_model
        self.settings.slack_model = c.slack_model
        self.settings.background_model = c.background_model or c.agent_model
        self.settings.jev_model = c.classifier_model
        self.settings.model_config_revision = self.revision

    async def defaults(self):
        chat, embeddings, generation = await asyncio.gather(
            self.gateway.models(), self.gateway.models(kind="embedding"), self.gateway.models(kind="generation"))
        if not chat:
            raise ValueError("This LiteLLM key has no available agent model.")
        if not embeddings:
            raise ValueError("This LiteLLM key has no available embedding model.")

        def choose(options, preferred, hints):
            ids = [m.id for m in options]
            if preferred in ids:
                return preferred
            for hint in hints:
                if selected := next((n for n in ids if hint in n.casefold()), None):
                    return selected
            return ids[0]

        return ModelConfiguration(
            agent_model=choose(chat, self.settings.litellm_model, ("luna", "mini", "haiku", "flash")),
            embedding_model=choose(embeddings, self.settings.memory_embedding_model, ("embedding", "embed")),
            background_model=choose(generation or chat, self.settings.background_model,
                                    ("luna", "mini", "haiku", "flash")),
            classifier_model=self.settings.jev_model,
        )

    async def current(self):
        return self.configuration or await self.defaults()

    async def describe(self):
        config = await self.current()
        return {"models": config.model_dump(), "revision": self.revision, "checks": self.last_checks,
                "effective": {"agent_model": config.agent_model, "embedding_model": config.embedding_model,
                              "slack_model": config.slack_model or config.agent_model,
                              "background_model": config.background_model or config.agent_model,
                              "classifier_model": config.classifier_model}}

    async def save(self, config: ModelConfiguration, *, expected_revision=None):
        async with self.lock:
            if expected_revision is not None and expected_revision != self.revision:
                raise ValueError("Model settings changed. Reload the settings before saving again.")
            defaults = await self.defaults()
            config = config.model_copy(update={
                "agent_model": config.agent_model or defaults.agent_model,
                "embedding_model": config.embedding_model or defaults.embedding_model,
            })
            # Validate the complete candidate before changing any live setting.
            unique_chat = {config.agent_model, config.slack_model or config.agent_model}
            for model in unique_chat:
                await self.gateway.validate_model(model)
            await self.gateway.validate_model(config.embedding_model, "embedding")
            await self.gateway.validate_model(config.background_model or config.agent_model, "generation")
            checked = await asyncio.gather(*(self.gateway.probe_model(m) for m in sorted(unique_chat)),
                                           self.gateway.probe_model(config.background_model or config.agent_model,
                                                                    "generation"),
                                           self.gateway.probe_model(config.embedding_model, "embedding"),
                                           self.gateway.probe_jev(config.classifier_model))
            jev = checked[-1]
            if jev["status"] != "ready":
                raise ValueError(jev["message"])
            old = self.configuration or ModelConfiguration(
                agent_model=self.settings.litellm_model, embedding_model=self.settings.memory_embedding_model,
                slack_model=self.settings.slack_model, background_model=self.settings.background_model,
                classifier_model=self.settings.jev_model)
            revision = self.revision + 1
            checks = {"agent_models": sorted(unique_chat), "embedding": checked[-2], "jev": jev}
            # Mark derived work stale with the new config durably before publishing the
            # settings revision. A crash can repeat work, never silently skip a migration.
            async with self.archive.lock:
                if (old.embedding_model != config.embedding_model
                            or old.classifier_model != config.classifier_model):
                    await self.archive.db.execute(
                        """UPDATE records SET status=CASE WHEN status='storing' THEN 'storing' ELSE 'pending' END,
                        attempts=0,next_attempt=0,work_lease='' WHERE deleted=0""")
                    if old.classifier_model != config.classifier_model:
                        await self.archive.db.execute("UPDATE records SET visibility='private' WHERE deleted=0")
                        await self.archive.db.execute("UPDATE members SET policy_version=policy_version+1")
                    await self.archive.db.commit()
                await self.store.setting(self.key, json.dumps({"models": config.model_dump(),
                                                              "revision": revision, "checks": checks}))
                self.configuration, self.revision, self.last_checks = config, revision, checks
                self.apply()
            return await self.describe()
