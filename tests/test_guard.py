"""Sensitive-data guard (owner: lead). Test case T7 in the test plan."""
from core.guard import scrub


def test_redacts_ein_ssn_password():
    r = scrub("EIN 12-3456789, SSN 123-45-6789, password: hunter2")
    assert "12-3456789" not in r.text and "123-45-6789" not in r.text and "hunter2" not in r.text
    assert {"EIN", "SSN", "PASSWORD"} <= set(r.findings)


def test_redacts_uei_but_not_plain_words():
    r = scrub("Our UEI is ABC123DEF456 and the program is HISTORICPRES")
    assert "ABC123DEF456" not in r.text
    assert "HISTORICPRES" in r.text  # letters only -> not a UEI


def test_leaves_normal_grant_text_alone():
    text = "Opportunity P25AS00257 closes 2025-06-12; award ceiling $15,000,000; CFDA 15.904"
    assert scrub(text).text == text
