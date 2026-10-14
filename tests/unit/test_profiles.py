"""Account identity stays private while generated avatars remain stable."""

from app.api.profiles import avatar_svg, mask_email


def test_email_mask_preserves_domain_and_hides_local_part():
    assert mask_email("gracy769@gmail.com") == "grac***9@gmail.com"
    assert mask_email("ab@example.org") == "a***@example.org"
    assert mask_email(None) is None
    assert mask_email("not-an-email") is None


def test_identicon_is_repeatable_and_symmetric():
    seed = "0123456789abcdef" * 5
    svg = avatar_svg(seed)
    assert svg == avatar_svg(seed)
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert 'x="12"' in svg and 'x="76"' in svg
