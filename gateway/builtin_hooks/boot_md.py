"""Built-in boot-md hook — run ~/.hermes/BOOT.md on gateway startup.

This hook is always registered. It silently skips if no BOOT.md exists.
To activate, create ``~/.hermes/BOOT.md`` with instructions for the
agent to execute on every gateway restart.

Example BOOT.md::

    # Startup Checklist

    1. Check if any cron jobs failed overnight
    2. Send a status update to Discord #general
    3. If there are errors in /opt/app/deploy.log, summarize them

The agent runs in a background thread so it doesn't block gateway
startup. If nothing needs attention, it replies with [SILENT] to
suppress delivery.
"""

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("hooks.boot-md")

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
BOOT_FILE = HERMES_HOME / "BOOT.md"


def _build_boot_prompt(content: str) -> str:
    """Wrap BOOT.md content in a system-level instruction."""
    return (
        "You are running a startup boot checklist. Follow the BOOT.md "
        "instructions below exactly.\n\n"
        "---\n"
        f"{content}\n"
        "---\n\n"
        "Execute each instruction. If you need to send a message to a "
        "platform, use the send_message tool.\n"
        "If nothing needs attention and there is nothing to report, "
        "reply with ONLY: [SILENT]"
    )


def _resolve_boot_agent_kwargs() -> dict:
    """Read model/provider from gateway config so the boot agent can make API calls.

    Config structure (config.yaml):
        model:
          default: claude-opus-4-6
          provider: anthropic
    """
    kwargs = {}
    try:
        from gateway.run import _resolve_gateway_model, _load_gateway_config
        config = _load_gateway_config()
        model = _resolve_gateway_model(config)
        if model:
            kwargs["model"] = model

        # Provider lives under model.provider in config.yaml
        model_cfg = config.get("model", {})
        if isinstance(model_cfg, dict):
            provider = model_cfg.get("provider", "")
            if provider:
                kwargs["provider"] = provider
    except Exception as e:
        logger.warning("Could not resolve boot agent config: %s", e)
    return kwargs


def _run_boot_agent(content: str) -> None:
    """Spawn a one-shot agent session to execute the boot instructions."""
def _load_model_config() -> dict:
    """Read model and provider from ~/.hermes/config.yaml."""
    try:
        import yaml as _yaml
        config_path = HERMES_HOME / "config.yaml"
        if config_path.exists():
            cfg = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            model_cfg = cfg.get("model", {})
            if isinstance(model_cfg, dict):
                return {
                    "model": model_cfg.get("default", ""),
                    "provider": model_cfg.get("provider", ""),
                }
    except Exception as e:
        logger.warning("boot-md: could not read config.yaml: %s", e)
    return {}


def _run_boot_agent(content: str) -> None:
    """Spawn a one-shot agent session to execute the boot instructions."""
    try:
        from run_agent import AIAgent

        model_cfg = _load_model_config()
        prompt = _build_boot_prompt(content)
        agent = AIAgent(
            model=model_cfg.get("model", ""),
            provider=model_cfg.get("provider", ""),
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            max_iterations=20,
        )
        result = agent.run_conversation(prompt)
        response = result.get("final_response", "")
        if response and "[SILENT]" not in response:
            logger.info("boot-md completed: %s", response[:200])
        else:
            logger.info("boot-md completed (nothing to report)")
    except Exception as e:
        logger.error("boot-md agent failed: %s", e)


async def handle(event_type: str, context: dict) -> None:
    """Gateway startup handler — run BOOT.md if it exists."""
    if not BOOT_FILE.exists():
        return

    content = BOOT_FILE.read_text(encoding="utf-8").strip()
    if not content:
        return

    logger.info("Running BOOT.md (%d chars)", len(content))

    # Run in a background thread so we don't block gateway startup.
    thread = threading.Thread(
        target=_run_boot_agent,
        args=(content,),
        name="boot-md",
        daemon=True,
    )
    thread.start()
