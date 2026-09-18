"""Headless rewrite backends for the Juniper skill factory."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess  # nosec B404 - This module starts fixed local commands without a shell.
from pathlib import Path

from src.juniper_skills.orchestrate.models import BackendProbe
from src.juniper_skills.rewrite import (
    CardClassMark,
    KnowledgeCard,
    PromptTemplateBuilder,
    RewriteBackend,
    RewriteResult,
    RewriteWorkPacket,
)

logger = logging.getLogger(__name__)  # Use a module logger so library logs keep their source name.


class BackendDiscovery:
    """Find a real headless rewrite backend on this machine."""

    def __init__(self, packet_dir: Path | None = None) -> None:
        """Initialize the BackendDiscovery instance."""
        self.packet_dir = packet_dir or Path("data") / "juniper_skills" / "rewrite_packets"  # Store packet fallback.

    def choose(self) -> tuple[RewriteBackend, tuple[BackendProbe, ...]]:
        """Run the choose operation."""
        logger.info("Discovering a headless rewrite backend")  # Log before probing local tools.
        probes = [self._copilot_probe(), self._gh_copilot_probe(), self._ollama_probe()]  # Test known local options.
        backend = self._backend_for(probes)  # Select the first true language-model path.
        logger.debug("Selected rewrite backend %s", backend.__class__.__name__)  # Record the selected backend type.
        return backend, tuple(probes)  # Return backend and probe evidence for the report.

    def _backend_for(self, probes: list[BackendProbe]) -> RewriteBackend:
        for probe in probes:  # Preserve probe order from most integrated to most local.
            if probe.available and probe.command:  # Use only probes that returned an executable command.
                return SubprocessAgentBackend(probe.command)  # Build the subprocess backend behind the seam.
        return PacketFileBackend(self.packet_dir)  # Fall back to external-agent packet files honestly.

    def _copilot_probe(self) -> BackendProbe:
        logger.info("Checking GitHub Copilot CLI for non-interactive prompt support")  # Log before command lookup.
        path = shutil.which("copilot")  # Use PATH discovery without starting a model call.
        if path is None:  # Stop if the CLI is not installed.
            return BackendProbe("copilot", False, "copilot was not found on PATH")
        result = self._run((path, "--help"))  # Read help text to verify headless mode.
        available = "--prompt" in result and "non-interactive" in result.lower()  # Require documented prompt mode.
        evidence = "copilot --help lists -p/--prompt for non-interactive mode" if available else result[:200]
        return BackendProbe("copilot", available, evidence, (path, "-s", "-p"))  # Return command prefix for calls.

    def _gh_copilot_probe(self) -> BackendProbe:
        logger.info("Checking gh copilot for a headless prompt command")  # Log before extension probe.
        path = shutil.which("gh")  # Find the GitHub CLI path for extension checks.
        if path is None:  # Stop if gh is not installed.
            return BackendProbe("gh copilot", False, "gh was not found on PATH")
        result = self._run((path, "copilot", "--help"))  # Check the extension help directly.
        available = "suggest" in result.lower() and "explain" in result.lower()  # gh-copilot is shell-help oriented.
        evidence = "gh copilot exists, but it exposes shell suggest and explain, not bulk rewrite prompt"
        return BackendProbe("gh copilot", False, evidence if available else result[:200])  # Do not fake a rewrite path.

    def _ollama_probe(self) -> BackendProbe:
        logger.info("Checking Ollama for local rewrite models")  # Log before local model discovery.
        path = shutil.which("ollama")  # Find Ollama without assuming it is installed.
        if path is None:  # Stop if the executable is absent.
            return BackendProbe("ollama", False, "ollama was not found on PATH")
        result = self._run((path, "list"))  # Read local model list without downloading anything.
        model = self._first_model(result)  # Pick the first available model when one exists.
        if model is None:  # A daemon with no model cannot rewrite text.
            return BackendProbe("ollama", False, "ollama list returned no local models")
        return BackendProbe("ollama", True, f"ollama list found local model {model}", (path, "run", model))

    def _first_model(self, output: str) -> str | None:
        rows = [line.split() for line in output.splitlines()[1:] if line.strip()]  # Ignore the Ollama header row.
        return rows[0][0] if rows and rows[0] else None  # Return the first model name for deterministic selection.

    def _run(self, command: tuple[str, ...]) -> str:
        try:
            result = subprocess.run(  # nosec B603 - The command comes from trusted PATH discovery.
                command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=False
            )  # Read help text.
        except OSError as error:
            logger.debug("Backend probe failed to start: %s", error)  # Record why this probe failed.
            return str(error)  # Return a short diagnostic for the probe evidence.
        except subprocess.TimeoutExpired:
            logger.debug("Backend probe timed out for %s", command[0])  # Record the timed-out tool.
            return "probe timed out"  # Return bounded evidence for the report.
        return (result.stdout + result.stderr).strip()  # Return all text because help can use either stream.


class SubprocessAgentBackend(RewriteBackend):
    """Rewrite one packet by calling a real headless agent subprocess."""

    def __init__(self, command_prefix: tuple[str, ...]) -> None:
        """Initialize the SubprocessAgentBackend instance."""
        self.command_prefix = command_prefix  # Store the executable and fixed arguments.
        self.prompt_builder = PromptTemplateBuilder()  # Reuse the locked rewrite prompt template.

    def rewrite(self, packet: RewriteWorkPacket) -> RewriteResult:
        """Run the rewrite operation."""
        logger.info("Running subprocess rewrite backend")  # Log before the model subprocess starts.
        prompt = self.prompt_builder.build(packet)  # Build the copyright-safe rewrite instruction.
        output = self._invoke(prompt)  # Call the selected real backend.
        cards = self._cards(output, packet)  # Parse the contract list-item output.
        logger.debug("Subprocess rewrite backend returned %d cards", len(cards))  # Record the parsed card count.
        return RewriteResult(tuple(cards), packet.detected_commands, tuple())  # Return publishable cards and commands.

    def _invoke(self, prompt: str) -> str:
        command = (*self.command_prefix, prompt)  # Append the prompt for Copilot-style `-p` commands.
        if self.command_prefix[:2] and self.command_prefix[1] == "run":  # Ollama reads prompts on standard input.
            command = self.command_prefix  # Keep the Ollama command prefix unchanged.
        result = subprocess.run(  # nosec B603 - The command prefix comes from a verified local backend probe.
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if result.returncode != 0:  # Surface a failed model call instead of pretending success.
            raise RuntimeError(result.stderr.strip() or "rewrite subprocess failed")  # Preserve subprocess evidence.
        return result.stdout.strip()  # Return only the model response for card parsing.

    def _cards(self, output: str, packet: RewriteWorkPacket) -> list[KnowledgeCard]:
        cards: list[KnowledgeCard] = []  # Collect parseable cards from the model output.
        for line in output.splitlines():  # Parse line by line so extra text can be ignored safely.
            card = self._card(line.strip(), packet)  # Try to convert one line to a knowledge card.
            if card is not None:  # Keep only contract-shaped cards.
                cards.append(card)  # Add the parsed card to the result.
        if not cards:  # A model response with no cards cannot publish a topic.
            raise RuntimeError("rewrite subprocess returned no knowledge cards")  # Fail for retry or human review.
        return cards  # Return all parsed cards.

    def _card(self, line: str, packet: RewriteWorkPacket) -> KnowledgeCard | None:
        prefix = line.removeprefix("-").strip()  # Accept Markdown list items with a leading dash.
        for mark in CardClassMark:  # Match only locked class marks.
            token = f"**{mark.value}**"  # Support the prompt's preferred bold mark.
            plain = f"{mark.value}:"  # Support the contract colon mark.
            if prefix.startswith(token):  # Parse the bold class mark form.
                return KnowledgeCard(mark, prefix[len(token) :].strip(), self._citation(packet))  # Return a card.
            if prefix.startswith(plain):  # Parse the colon class mark form.
                return KnowledgeCard(mark, prefix[len(plain) :].strip(), self._citation(packet))  # Return a card.
        return None  # Ignore commentary that is not a card.

    def _citation(self, packet: RewriteWorkPacket) -> str:
        return f"[{packet.citation_key} {packet.page_range}]"  # Use the packet citation when the model omits one.


class PacketFileBackend(RewriteBackend):
    """Write rewrite packets for an external agent fleet and read completed cards."""

    def __init__(self, packet_dir: Path) -> None:
        """Initialize the PacketFileBackend instance."""
        self.packet_dir = packet_dir  # Store the durable packet exchange directory.
        self.prompt_builder = PromptTemplateBuilder()  # Reuse the same prompt text as subprocess agents.

    def rewrite(self, packet: RewriteWorkPacket) -> RewriteResult:
        """Run the rewrite operation."""
        logger.info("Writing rewrite packet for external processing")  # Log before durable packet output.
        self.packet_dir.mkdir(parents=True, exist_ok=True)  # Create the queue directory if it is absent.
        packet_path = self._packet_path(packet)  # Resolve the deterministic packet file path.
        response_path = packet_path.with_suffix(".response.json")  # Define the external-agent response path.
        packet_path.write_text(self._packet_text(packet), encoding="utf-8")  # Write the prompt and source safely.
        logger.debug("Wrote rewrite packet %s", packet_path)  # Record the created packet path.
        if not response_path.exists():  # Refuse to invent language-model output.
            raise RuntimeError(f"Rewrite response is not ready: {response_path}")  # Tell the runner to retry later.
        return self._read_response(response_path, packet)  # Load the completed external response.

    def _packet_path(self, packet: RewriteWorkPacket) -> Path:
        name = f"{packet.citation_key.lower()}-{abs(hash(packet.source_segment))}.prompt.md"  # Build a stable name.
        return self.packet_dir / name  # Keep packets in the durable exchange directory.

    def _packet_text(self, packet: RewriteWorkPacket) -> str:
        return self.prompt_builder.build(packet) + "\n"  # Store the exact task for the external agent.

    def _read_response(self, response_path: Path, packet: RewriteWorkPacket) -> RewriteResult:
        logger.info("Reading completed rewrite response %s", response_path)  # Log before reading external output.
        payload = json.loads(response_path.read_text(encoding="utf-8"))  # Read the external agent JSON result.
        cards = tuple(self._response_card(row) for row in payload.get("cards", []))  # Convert rows into cards.
        if not cards:  # A response without cards cannot publish useful content.
            raise RuntimeError(f"Rewrite response has no cards: {response_path}")  # Fail loudly for retry.
        logger.debug("Read %d cards from rewrite response", len(cards))  # Record the response size.
        return RewriteResult(cards, packet.detected_commands, tuple(payload.get("limitations", ())))  # Return result.

    def _response_card(self, row: dict[str, str]) -> KnowledgeCard:
        mark = CardClassMark(str(row.get("mark", "INFO")))  # Validate the card mark against the locked values.
        return KnowledgeCard(mark, str(row["fact"]), str(row["citation_key"]))  # Return the external card.
