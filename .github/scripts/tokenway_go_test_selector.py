"""Shared Go -run selector generation for targeted failure reruns."""


def go_regex_escape(value: str) -> str:
    """Escape Go RE2 metacharacters without escaping ordinary spaces or hyphens."""
    metacharacters = set(r"\\.+*?()|[]{}^$")
    return "".join("\\" + character if character in metacharacters else character for character in value)


def targeted_test_pattern(test_names: set[str]) -> str:
    """Return a Go-compatible pattern for the top-level tests owning these failures.

    Go's -run splits patterns on '/', so a failed subtest is routed to its
    top-level test function. This reruns only the affected test functions.
    """
    roots = sorted({name.split("/", 1)[0] for name in test_names})
    if not roots:
        raise ValueError("at least one failed Go test name is required")
    return "^(" + "|".join(go_regex_escape(name) for name in roots) + ")$"
