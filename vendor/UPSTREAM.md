The `product-team-knowledge` directory vendors the Node MCP runtime from
https://github.com/BerriAI/product-team-knowledge at commit
`6a8b19d` on branch `feat/memory-metadata-semantic-search`.

It includes metadata filtering, sorting, pagination, and optional content-addressed
semantic/hybrid retrieval. No private memory records are bundled. LiteLLM Memory permits
only the catalog, search, and read tools at the SDK boundary.

To update: copy `src/`, `bin/`, `prompts/`, `package.json`, and `package-lock.json`
from the reviewed upstream checkout. Run the upstream Node tests and LiteLLM Memory tests.
