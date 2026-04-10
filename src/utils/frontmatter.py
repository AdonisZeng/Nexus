"""YAML frontmatter parsing utility."""
import re
import yaml

FRONTMATTER_PATTERN = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL | re.MULTILINE
)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Parse YAML frontmatter from markdown text.

    Returns (frontmatter_dict, body_str).
    On YAML parse error, returns ({}, original_text).
    """
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        return {}, text.strip()

    frontmatter_str = match.group(1)
    body = match.group(2).strip()
    try:
        frontmatter = yaml.safe_load(frontmatter_str) or {}
        return frontmatter, body
    except yaml.YAMLError:
        return {}, body


def serialize_frontmatter(data: dict, body: str = "", title: str = None) -> str:
    """
    Serialize dict as YAML frontmatter with optional body.

    Args:
        data: Frontmatter dictionary
        body: Optional body content
        title: Optional title (adds "# title" line after frontmatter)

    Returns:
        Frontmatter-formatted string: "---\n<yaml>\n---\n[<title>\n]<body>"
    """
    yaml_content = yaml.dump(data, allow_unicode=True, default_flow_style=False).strip()
    parts = ["---", yaml_content, "---"]
    if title:
        parts.extend(["", f"# {title}"])
    if body:
        parts.extend(["", body])
    return "\n".join(parts)
