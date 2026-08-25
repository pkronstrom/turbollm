"""Hermes Agent harness — sets HERMES_HOME with config pointing at turbo server."""

import os
import shutil
import subprocess
import tempfile

from turbollm.harnesses import register


@register("hermes")
class HermesHarness:
    def __init__(self, config: dict):
        self.name = "hermes"
        self._binary = config.get("binary", "hermes")
        self.install_hint = config.get(
            "install",
            "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash",
        )

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def launch(self, model_id: str, port: int, model: dict) -> None:
        # Hermes ignores OPENAI_BASE_URL — it reads model.base_url from
        # ~/.hermes/config.yaml.  Create a temp HERMES_HOME with a config
        # pointing at the turbo server, symlinking everything else from
        # the real home so memories/sessions/SOUL.md are preserved.
        with tempfile.TemporaryDirectory(prefix="turbo-hermes-") as tmp:
            real_home = os.path.expanduser("~/.hermes")
            if os.path.isdir(real_home):
                for item in os.listdir(real_home):
                    if item == "config.yaml":
                        continue
                    os.symlink(os.path.join(real_home, item), os.path.join(tmp, item))

            config_yaml = (
                f"model:\n"
                f"  default: {model_id}\n"
                f"  base_url: http://127.0.0.1:{port}/v1\n"
                f"  provider: auto\n"
            )
            with open(os.path.join(tmp, "config.yaml"), "w") as f:
                f.write(config_yaml)

            env = os.environ.copy()
            env["HERMES_HOME"] = tmp
            env.setdefault("OPENAI_API_KEY", "sk-local-no-auth-needed")
            subprocess.run([self._binary, "chat"], env=env)
