# Third-party notices

LiteLLM Memory is a LiteLLM Labs product. It is not an official Ente client and is not affiliated with Ente's hosted service.

## Ente Museum

The portable image copies the **unmodified Ente Museum server executable and runtime assets** from `ghcr.io/ente/server`. Museum provides authenticated encrypted-entity storage backed by PostgreSQL. LiteLLM Memory uses its HTTP API; it does not write to Ente's database schema directly.

- License: GNU Affero General Public License, version 3. A copy is in [LICENSE](LICENSE).
- Corresponding upstream source for the pinned executable: [ente-io/ente at 163f45c26f130090abed03649530f53d2d976565](https://github.com/ente-io/ente/tree/163f45c26f130090abed03649530f53d2d976565).
- Pinned image index: `sha256:3511ff25db3bf950abeb3983928eeadeed7f53b690ee94704c14be72fa719b1d`.
- Verified Linux amd64 child: `sha256:e8770139ea7d58bd9b3661d32f1239473a25079bd3f034aac1effe25de87be71`.
- Image `GIT_COMMIT` and the running server's `/ping` reported the source revision above.

`litellm_memory/crypto.py` implements compatible libsodium secretstream and secretbox formats. It is not the Ente Photos application, and hosted key custody is different from Ente Photos' client-side, zero-knowledge design. Read [security.md](docs/security.md).

## Other components

- [LiteLLM](https://github.com/BerriAI/litellm) is an external OpenAI-compatible gateway configured by the operator; this image does not bundle a LiteLLM server.
- [PyNaCl](https://github.com/pyca/pynacl) supplies the libsodium bindings used for encryption.
- [LiteAgents SDK](https://github.com/BerriAI/liteagents) runs the query tool loop. Requests use the configured LiteLLM endpoint.
- [Pydantic](https://github.com/pydantic/pydantic) validates data contracts; [Logfire](https://github.com/pydantic/logfire) provides optional operational tracing. The core does not require a Logfire cloud account.
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) supplies remote MCP transport.
- [Marked](https://github.com/markedjs/marked) and [DOMPurify](https://github.com/cure53/DOMPurify) render sanitized Markdown. Their bundled licenses are in `static/vendor/marked.LICENSE` and `static/vendor/dompurify.LICENSE`.

Python dependencies and exact resolved versions are in `uv.lock`; packages retain their own license metadata.

## Interface assets

- Inter variable Latin font, distributed by `@fontsource-variable/inter` 5.3.0, SIL Open Font License. License: `static/vendor/inter.LICENSE`.
- Lucide icon paths from `lucide-static` 1.47.0, ISC license. License: `static/vendor/lucide.LICENSE`. Only the icons used by the interface are included.

Assets are served locally; the interface makes no font-CDN requests.
