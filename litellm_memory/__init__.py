"""LiteLLM Memory: product memory research in Slack and on the web."""
import os

# Settings owns .env loading. LiteLLM's DEV default otherwise imports .env
# into os.environ, overriding isolated Settings(_env_file=None) instances.
os.environ.setdefault('LITELLM_MODE', 'PRODUCTION')
