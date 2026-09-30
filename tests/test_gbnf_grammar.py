"""Tests unitarios para la Gramática GBNF y Validador (GBNFValidator)."""

import pytest

from jev_ultrafast.gbnf_grammar import (
    GBNFValidator,
    build_candidate_constrained_grammar,
)


def test_build_candidate_constrained_grammar():
    grammar = build_candidate_constrained_grammar(max_candidate_id=5)
    assert 'candidate_id ::= "1" | "2" | "3" | "4" | "5"' in grammar

    grammar_large = build_candidate_constrained_grammar(max_candidate_id=25)
    assert "candidate_id ::= [1-9] [0-9]*" in grammar_large


def test_validate_action_payload_valid():
    valid_json = '{"action": "click", "candidate_id": 3, "value": "", "confidence": 0.95, "reasoning": "Buscador"}'
    parsed = GBNFValidator.validate_action_payload(valid_json, max_candidate_id=10)

    assert parsed["action"] == "click"
    assert parsed["candidate_id"] == 3
    assert parsed["confidence"] == 0.95
    assert parsed["reasoning"] == "Buscador"


def test_validate_action_payload_invalid_json():
    invalid_json = '{"action": "click", "candidate_id": 3,}'  # Trailing comma
    with pytest.raises(ValueError, match="Sintaxis JSON inválida"):
        GBNFValidator.validate_action_payload(invalid_json)


def test_validate_action_payload_unknown_action():
    bad_action_json = '{"action": "teleport", "candidate_id": 1, "value": "", "confidence": 1.0}'
    with pytest.raises(ValueError, match="Acción inválida 'teleport'"):
        GBNFValidator.validate_action_payload(bad_action_json)


def test_validate_action_payload_candidate_out_of_bounds():
    out_of_bounds = '{"action": "type", "candidate_id": 15, "value": "test", "confidence": 0.90}'
    with pytest.raises(ValueError, match="candidate_id 15 fuera de rango"):
        GBNFValidator.validate_action_payload(out_of_bounds, max_candidate_id=5)
