"""Django template syntax that renders instead of disappearing.

`{# ... #}` is SINGLE-LINE. Django's lexer matches the tag only when the
opening and closing braces sit on one line; a comment wrapped across two
renders verbatim into the page, at body size, in front of the user.

That is not a hypothetical. Six of these shipped — four on the
assumptions page, one on its wait page, one on the deal detail page —
putting CSS utility classes, `forms.py`, and paragraphs of internal
design rationale on the primary data-entry surface of a live app.

Nothing else catches it: the template compiles, every test that renders
the page still passes, and the leaked text is invisible to any assertion
that is not looking for it. The sweep is the only guard, so it is
repo-wide rather than a list of the six files that were wrong once.
"""

import pathlib
import re

import pytest

TEMPLATE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "webapp"


def _templates():
    return sorted(TEMPLATE_ROOT.rglob("*.html"))


def test_there_are_templates_to_sweep():
    """A sweep over an empty set passes for the wrong reason."""
    assert len(_templates()) > 10


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.name)
def test_no_multiline_django_comment(path):
    text = path.read_text()
    for match in re.finditer(r"\{#", text):
        end = text.find("#}", match.start())
        line = text[:match.start()].count("\n") + 1
        assert end != -1, f"{path}:{line} opens {{# and never closes it"
        assert "\n" not in text[match.start():end + 2], (
            f"{path}:{line} wraps a {{# #}} comment across lines — Django "
            f"renders it as visible body text. Use "
            f"{{% comment %}}...{{% endcomment %}}.")
