"""Generate the same Slack manifest used by the setup page, without credentials."""
import json
import sys

from litellm_memory.slack_manifest import slack_manifest as manifest

if __name__ == "__main__":
    print(json.dumps(manifest(sys.argv[1].rstrip("/")), indent=2))
