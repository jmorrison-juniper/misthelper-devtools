#!/usr/bin/env node
// Validate every Mermaid block before GitHub renders the documentation.

import { readFile } from "node:fs/promises"; // Read Markdown files without blocking the parser.
import { readdir } from "node:fs/promises"; // Find Markdown files below the documentation root.
import { resolve, join } from "node:path"; // Build platform-safe paths for local and CI runs.
import { JSDOM } from "jsdom"; // Provide the DOM that Mermaid needs during parsing.

class MermaidLintRunner {
  // Store the parser configuration and the failure list for one run.
  constructor(rootDirectory, extraFiles) {
    this.rootDirectory = resolve(rootDirectory); // Resolve the scan root once for stable output.
    this.extraFiles = extraFiles.filter((filePath) => filePath).map((filePath) => resolve(filePath)); // Resolve additional Markdown files.
    this.failures = []; // Keep every failure so the report names all broken blocks.
    this.blockCount = 0; // Count blocks to prove that the gate scanned the repository.
    this.filesWithBlocks = 0; // Count only Markdown files that contain Mermaid blocks.
  }

  // Install browser globals before Mermaid loads its DOM-dependent modules.
  async loadMermaid() {
    const dom = new JSDOM("<!doctype html><html><body></body></html>"); // Create the smallest usable browser document.
    globalThis.window = dom.window; // Expose the browser window to Mermaid.
    globalThis.document = dom.window.document; // Expose the browser document to Mermaid.
    Object.defineProperty(globalThis, "navigator", { value: dom.window.navigator, configurable: true }); // Support Node versions with a read-only navigator getter.
    globalThis.DOMPurify = dom.window.DOMPurify; // Preserve the sanitizer hook when Mermaid requests it.
    return (await import("mermaid")).default; // Load Mermaid only after the DOM exists.
  }

  // Collect Markdown files from the documentation tree and explicit extra files.
  async collectFiles() {
    const files = []; // Store the files in deterministic order.
    await this.collectDirectory(this.rootDirectory, files); // Scan the documentation directory recursively.
    files.push(...this.extraFiles); // Include Markdown files outside the documentation directory.
    return [...new Set(files)].sort(); // Remove duplicates and stabilize the report order.
  }

  // Walk one directory and append Markdown files to the result list.
  async collectDirectory(directoryPath, files) {
    const entries = await readdir(directoryPath, { withFileTypes: true }); // Read one directory level.
    for (const entry of entries) { // Visit each child entry.
      const entryPath = join(directoryPath, entry.name); // Build the child path safely.
      if (entry.isDirectory()) await this.collectDirectory(entryPath, files); // Continue through nested documentation folders.
      if (entry.isFile() && entry.name.endsWith(".md")) files.push(entryPath); // Scan only Markdown documentation.
    }
  }

  // Extract fenced Mermaid blocks and their starting line number.
  extractBlocks(content) {
    const blocks = []; // Store each block with its source line.
    const pattern = /```mermaid\s*\r?\n([\s\S]*?)```/gi; // Match Mermaid fences without changing their content.
    for (const match of content.matchAll(pattern)) { // Process every Mermaid fence in the file.
      const line = content.slice(0, match.index).split(/\r?\n/).length; // Calculate the fence line for failures.
      blocks.push({ text: match[1], line }); // Keep the text and line together.
    }
    return blocks; // Return all blocks found in the file.
  }

  // Parse every Mermaid block and record the source of each failure.
  async validateFile(filePath, mermaid) {
    const content = await readFile(filePath, "utf8"); // Read one Markdown file for validation.
    const blocks = this.extractBlocks(content); // Extract only Mermaid content from the file.
    this.blockCount += blocks.length; // Count blocks before validation starts.
    if (blocks.length) this.filesWithBlocks += 1; // Count files that contribute Mermaid blocks.
    for (const block of blocks) { // Validate blocks in source order.
      try { await mermaid.parse(block.text, { suppressErrors: false }); } // Ask Mermaid to parse the block.
      catch (error) { this.failures.push({ filePath, line: block.line, error }); } // Keep the file and line for diagnosis.
    }
  }

  // Run the complete validation and return the process exit code.
  async run() {
    const mermaid = await this.loadMermaid(); // Load Mermaid after browser globals exist.
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict" }); // Disable page scanning and keep parsing safe.
    const files = await this.collectFiles(); // Find every Markdown input.
    for (const filePath of files) await this.validateFile(filePath, mermaid); // Validate every discovered file.
    return this.report(); // Print a compact result and return the correct status.
  }

  // Print a success or failure report that identifies every broken block.
  report() {
    for (const failure of this.failures) console.error(`MERMAID ERROR: ${failure.filePath}:${failure.line} ${failure.error.message}`); // Name each broken source.
    if (this.failures.length) return 1; // Fail the gate when one block does not parse.
    console.log(`OK: ${this.blockCount} Mermaid blocks parsed across ${this.filesWithBlocks} Markdown files`); // Confirm the scan coverage.
    return 0; // Pass the gate when every block parses.
  }
}

const rootIndex = process.argv.indexOf("--docs-dir"); // Find an optional documentation root.
const rootDirectory = rootIndex >= 0 ? process.argv[rootIndex + 1] : "documentation"; // Use the repository default when omitted.
const hasExtraFileFlag = process.argv.includes("--extra-file"); // Detect when a caller overrides the default.
const extraFiles = []; // Store each optional Markdown file.
for (let index = 0; index < process.argv.length; index += 1) { // Read repeated extra-file flags.
  if (process.argv[index] === "--extra-file" && process.argv[index + 1]) extraFiles.push(process.argv[index + 1]); // Keep each requested extra file.
}
if (!hasExtraFileFlag && !extraFiles.length) extraFiles.push("README.md"); // Scan README diagrams by default.
const runner = new MermaidLintRunner(rootDirectory, extraFiles); // Create one isolated validator instance.
process.reallyExit(await runner.run()); // Run the gate and expose its result to CI.
