import pytest

from job_hunter.config import load_config

VALID = """
searches:
  - name: py-blr
    keywords: "python developer"
    location: "Bengaluru, India"
    posted_within: week
    experience_levels: [entry, associate]
    workplace: [remote]
    job_types: [full_time]
    max_pages: 2
filters:
  title_exclude: ["principal"]
score_keywords:
  Python: 3
rate_limit:
  min_delay_seconds: 1
  jitter_seconds: 0
"""


def test_load_config_maps_linkedin_codes(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text(VALID)
    cfg = load_config(path)

    spec = cfg.searches[0]
    params = spec.query_params()
    assert params["keywords"] == "python developer"
    assert params["f_TPR"] == "r604800"      # week
    assert params["f_E"] == "2,3"            # entry, associate
    assert params["f_WT"] == "2"             # remote
    assert params["f_JT"] == "F"             # full_time

    assert cfg.filters.title_exclude == ["principal"]
    assert cfg.score_keywords == {"python": 3}  # lowercased
    assert cfg.sqlite_path == "data/jobs.db"    # default


def test_load_config_rejects_bad_values(tmp_path):
    path = tmp_path / "cfg.yaml"

    path.write_text("searches: []")
    with pytest.raises(ValueError, match="at least one"):
        load_config(path)

    path.write_text("""
searches:
  - {name: a, keywords: x, location: y, posted_within: fortnight}
""")
    with pytest.raises(ValueError, match="posted_within"):
        load_config(path)

    path.write_text("""
searches:
  - {name: a, keywords: x, location: y}
  - {name: a, keywords: z, location: y}
""")
    with pytest.raises(ValueError, match="unique"):
        load_config(path)
