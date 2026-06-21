"""Anomaly detection module for TM1 cubes.

Layered design:
  - Layer 1: builtin integrity rules (negative values, missing data, ...)
  - Layer 2: user-defined variance rules loaded from YAML
  - Severity scoring + dismissal memory keep noise down

LLM is used at config-time (to draft rules) and at result-time (to write
commentary), never at runtime to judge whether something is anomalous.
"""
