"""Architecture intelligence (Phase 2): layers, tours, symbol explanations and search.

Everything is computed deterministically from a project *snapshot* (its File / Class /
Function / Module nodes and relationships) read with two bounded, project-scoped queries.
The LLM is only used, optionally, to put retrieved evidence into words.
"""
