"""Shared Slack installation contract for the setup page and command line."""

BOT_SCOPES = ["app_mentions:read", "chat:write", "channels:read", "channels:manage", "groups:read",
              "groups:write", "im:history", "im:read", "reactions:write", "channels:history",
              "groups:history", "mpim:read", "mpim:history", "assistant:write", "users:read", "users:read.email"]


def slack_manifest(base_url):
    return {
        "display_information": {"name": "LiteLLM Memory", "description": "See what your team is working on. Ask about anything shared with you.",
                                "background_color": "#2b302b"},
        "features": {"bot_user": {"display_name": "memory", "always_online": False},
                     "agent_view": {"agent_description": "Ask about work and decisions you can access.",
                                    "suggested_prompts": [
                                        {"title": "My recent work", "message": "What did I work on recently?"},
                                        {"title": "Team updates", "message": "What has the team shared recently?"}]},
                     "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False}},
        "oauth_config": {"redirect_urls": [base_url + "/slack/install/callback", base_url + "/auth/slack/callback"],
                         "scopes": {"bot": BOT_SCOPES}},
        "settings": {"event_subscriptions": {"request_url": base_url + "/slack/events",
                      "bot_events": ["app_mention", "message.im", "message.mpim", "message.channels", "message.groups",
                                     "member_joined_channel", "member_left_channel", "user_change"]},
                     "org_deploy_enabled": False, "socket_mode_enabled": False, "token_rotation_enabled": False},
    }

