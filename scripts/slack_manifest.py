"""Generate the Slack manifest without embedding credentials."""
import json
import sys

SCOPES = ["app_mentions:read", "chat:write", "channels:read", "channels:manage",
          "groups:read", "groups:write", "im:history", "im:read", "reactions:write",
          "channels:history", "groups:history", "mpim:read", "mpim:history", "assistant:write"]


def manifest(base_url):
    return {
        "display_information": {"name": "litemem", "description": "Ask about your work, decisions, and memories.",
                                "background_color": "#25252d"},
        "features": {"bot_user": {"display_name": "litemem", "always_online": True},
                     "agent_view": {"agent_description": "Ask litemem about work, decisions, and memories you can access.",
                                    "suggested_prompts": [
                                        {"title": "Recent decisions", "message": "What has Moe decided recently?"},
                                        {"title": "litemem", "message": "What decisions shaped litemem?"}]},
                     "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False}},
        "oauth_config": {"redirect_urls": [base_url + "/slack/install/callback", base_url + "/auth/slack/callback"],
                         "scopes": {"bot": SCOPES}},
        "settings": {"event_subscriptions": {"request_url": base_url + "/slack/events",
                      "bot_events": ["app_mention", "message.im", "message.mpim", "message.channels", "message.groups",
                                     "member_joined_channel", "member_left_channel"]},
                     "org_deploy_enabled": False, "socket_mode_enabled": False, "token_rotation_enabled": False},
    }


if __name__ == "__main__":
    print(json.dumps(manifest(sys.argv[1].rstrip("/")), indent=2))
