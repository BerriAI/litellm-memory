"""LiteAgents configuration shared by both memory query entry points."""

import inspect

from liteagents import AssistantMessage, LiteAgentOptions, TextBlock, Tool, UserMessage
from pydantic import TypeAdapter, validate_call


class function_tool(Tool):
    """Expose an annotated async Python function through LiteAgents' Tool API."""

    def __init__(self, function):
        self.name = function.__name__
        self.description = inspect.getdoc(function) or ''
        self.input_schema = TypeAdapter(function).json_schema()
        self._function = validate_call(function)

    async def execute(self, input):
        return await self._function(**input)


def agent_options(settings, model, **kwargs):
    return LiteAgentOptions(
        # Treat even provider-looking catalog IDs as opaque gateway aliases.
        model='litellm_proxy/' + model,
        stream=True,
        max_turns=settings.max_agent_turns,
        model_kwargs={
            'api_base': settings.litellm_base_url.rstrip('/'),
            'api_key': settings.litellm_api_key,
            'timeout': 90,
            'num_retries': 0,
            'extra_headers': {'x-litellm-enable-message-redaction': 'true'},
            'extra_body': {'no-log': True},
            'no-log': True,
        },
        **kwargs,
    )


def chat_history(history):
    result = []
    for message in history or []:
        if message['role'] == 'user':
            result.append(UserMessage(content=message['content']))
        elif message['role'] == 'assistant':
            result.append(AssistantMessage(content=[TextBlock(message['content'])], model=''))
        else:
            raise ValueError('Chat history may contain only user and assistant messages.')
    return result


class RunOutput:
    def __init__(self):
        self.text = None
        self.input_tokens = self.output_tokens = self.requests = 0

    def observe(self, message):
        self.requests += 1
        usage = message.usage or {}
        self.input_tokens += usage.get('input_tokens', 0)
        self.output_tokens += usage.get('output_tokens', 0)
        self.text = None
        if message.stop_reason == 'max_tokens':
            raise RuntimeError('The model reached its output limit before finishing the answer.')
        if message.stop_reason != 'tool_use':
            self.text = ''.join(block.text for block in message.content if isinstance(block, TextBlock))

    def final_text(self):
        if self.text is None:
            raise RuntimeError('The agent reached its turn limit before finishing the answer.')
        return self.text
