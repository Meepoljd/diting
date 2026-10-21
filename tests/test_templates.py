"""PR-4: skill / ci-template."""

import yaml

from diting.templates import DEFAULT_PIP_SPEC, SKILL_MD, ci_workflow

from .test_cli import run, setup_concept


def test_skill_writes_and_is_idempotent(capsys, shop):
    setup_concept(capsys, shop)
    code, out = run(capsys, shop, "skill")
    assert code == 0 and out["status"] == "created"
    path = shop / ".diting/SKILL.md"
    assert path.read_text(encoding="utf-8") == SKILL_MD
    code, out = run(capsys, shop, "skill")
    assert code == 0 and out["status"] == "unchanged"


def test_skill_refuses_to_overwrite_local_edits(capsys, shop):
    setup_concept(capsys, shop)
    run(capsys, shop, "skill")
    path = shop / ".diting/SKILL.md"
    path.write_text("custom\n", encoding="utf-8")
    code, out = run(capsys, shop, "skill")
    assert code != 0 and "--force" in out["error"]
    assert path.read_text(encoding="utf-8") == "custom\n"
    code, out = run(capsys, shop, "skill", "--force")
    assert code == 0 and out["status"] == "updated"
    assert path.read_text(encoding="utf-8") == SKILL_MD


def test_skill_frontmatter():
    head = SKILL_MD.split("---")[1]
    meta = yaml.safe_load(head)
    assert meta["name"] == "diting" and "BEFORE" in meta["description"]
    for cmd in ("diting query", "diting impact", "diting sync", "diting confirm", "diting check"):
        assert cmd in SKILL_MD


def test_ci_template_is_valid_workflow():
    wf = yaml.safe_load(ci_workflow())
    steps = wf["jobs"]["diting-check"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    assert any(DEFAULT_PIP_SPEC in s.get("run", "") for s in steps)
    script = steps[-1]["run"]
    assert 'diting check --base "origin/${{ github.base_ref }}"' in script
    assert "{pip_spec}" not in ci_workflow("diting==1.0")
    assert "diting==1.0" in ci_workflow("diting==1.0")


def test_ci_template_write_file(capsys, shop):
    setup_concept(capsys, shop)
    target = shop / ".github/workflows/diting.yml"
    code, out = run(capsys, shop, "ci-template", "-o", str(target), "--pip", "diting==9")
    assert code == 0 and out["status"] == "created"
    text = target.read_text(encoding="utf-8")
    assert 'pip install "diting==9"' in text
