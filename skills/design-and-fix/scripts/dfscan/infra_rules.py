from __future__ import annotations

import posixpath
import re
from typing import Dict, List, Optional, Tuple

from .filecache import FileCache
from .imports import _read
from .model import FileInfo, Signal
from .registry import RuleMeta, register
from .text import strip_comments

# Infra rules (spec 9.3) read files the walker marks is_infra: Dockerfiles, k8s workload YAML,
# GitHub Actions / GitLab CI / CircleCI configs. Test and example folders are skipped.
# Comments are blanked before matching (Dockerfile: whole-line `#`; YAML: `#` outside quotes).


def _docker_text(raw: str) -> str:
    """Blank whole-line `#` comments (Dockerfile has no inline comments); keep line numbers."""
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in raw.split("\n"))


def _yaml_text(raw: str) -> str:
    return strip_comments(raw.replace("\r\n", "\n"), "Python")


def _is_dockerfile(fi: FileInfo) -> bool:
    low = posixpath.basename(fi.rel).lower()
    return low == "dockerfile" or low.startswith("dockerfile.") or low.endswith(".dockerfile")


def _is_ci(fi: FileInfo) -> bool:
    return fi.is_infra and not _is_dockerfile(fi) and (
        "/.github/workflows/" in "/" + fi.rel or fi.rel.endswith(".gitlab-ci.yml")
        or "/.circleci/" in "/" + fi.rel)


# ---------------------------------------------------------------- Dockerfile parsing
_INSTR_RE = re.compile(r"^[ \t]*([A-Za-z]+)[ \t]+(.*)$")
_STAGE_RE = re.compile(r"\bAS[ \t]+([\w.-]+)\s*$", re.I)


def _instructions(text: str) -> List[Tuple[int, str, str]]:
    """(line, INSTRUCTION, args) for each instruction line (continuations not joined)."""
    out = []
    for i, ln in enumerate(text.split("\n")):
        m = _INSTR_RE.match(ln)
        if m:
            out.append((i + 1, m.group(1).upper(), m.group(2).strip()))
    return out


def _from_image(args: str) -> str:
    """Image reference of a FROM line, without --platform=... flags and the AS stage name."""
    words = [w for w in args.split() if not w.startswith("--")]
    return words[0] if words else ""


# Final-stage base images that already run as a non-root user.
_NONROOT_BASE_RE = re.compile(r"nonroot|rootless|unprivileged|^cgr\.dev/|(?:^|/)bitnami/", re.I)


def rule_docker_root_user(fi: FileInfo, text: str) -> List[Signal]:
    """Final build stage with no USER instruction, or whose last USER is root / 0.

    False-positive risks: a base image that already switches to a non-root user is recognised only
    by name (nonroot, rootless, unprivileged, cgr.dev/, bitnami/); other such bases are flagged. A
    user set at run time (`docker run --user`, k8s securityContext.runAsUser) is not seen. A
    variable USER (`USER $APP_USER`) counts as non-root. A final stage built `FROM` an earlier
    stage inherits that stage's user (followed through stage chains).
    """
    ins = _instructions(text)
    froms = [k for k, (_, name, _) in enumerate(ins) if name == "FROM"]
    if not froms:
        return []
    # Stages: (FROM index, image, stage name, last USER (line, args) or None).
    stages: List[Tuple[int, str, str, Optional[Tuple[int, str]]]] = []
    for n, k in enumerate(froms):
        end = froms[n + 1] if n + 1 < len(froms) else len(ins)
        users = [(ln, args) for ln, name, args in ins[k + 1:end] if name == "USER"]
        sm = _STAGE_RE.search(ins[k][2])
        stages.append((k, _from_image(ins[k][2]).lower(), sm.group(1).lower() if sm else "",
                       users[-1] if users else None))
    last = froms[-1]
    from_line, _, from_args = ins[last]
    # Effective user of the final stage: its own last USER, else the stage it is built FROM.
    user: Optional[Tuple[int, str]] = None
    own = True
    idx = len(stages) - 1
    for _ in range(len(stages)):
        _, image, _, stage_user = stages[idx]
        if _NONROOT_BASE_RE.search(image):
            return []
        if stage_user is not None:
            user = stage_user
            break
        parent = next((j for j in range(idx - 1, -1, -1) if stages[j][2] and stages[j][2] == image),
                      None)
        if parent is None:
            break
        idx, own = parent, False
    if user is None:
        return [Signal("docker-root-user", "Security", "High", fi.rel, from_line,
                       "final stage has no USER: FROM " + from_args, "medium")]
    ln, args = user
    if args.split(":")[0].strip().strip("\"'") in ("root", "0"):
        if own:
            return [Signal("docker-root-user", "Security", "High", fi.rel, ln, "USER " + args,
                           "high")]
        return [Signal("docker-root-user", "Security", "High", fi.rel, from_line,
                       "final stage inherits USER %s: FROM %s" % (args, from_args), "medium")]
    return []


def rule_docker_latest_tag(fi: FileInfo, text: str) -> List[Signal]:
    """FROM image:latest or an untagged FROM image (floats to whatever latest is at build time).

    False-positive risks: none known for `:latest`; an untagged image that is really a stage of
    another file (docker buildx bake contexts) is flagged. Skipped: references to earlier `AS`
    stages, `scratch`, images given by build ARG (`${BASE}`) and digest-pinned images (@sha256).
    """
    out: List[Signal] = []
    stages = set()
    for ln, name, args in _instructions(text):
        if name != "FROM":
            continue
        image = _from_image(args)
        m = _STAGE_RE.search(args)
        if image and image.lower() not in stages and image.lower() != "scratch" \
                and "$" not in image and "@" not in image:
            last_seg = image.rsplit("/", 1)[-1]
            if ":" not in last_seg:
                out.append(Signal("docker-latest-tag", "Reliability", "Medium", fi.rel, ln,
                                  "FROM " + args, "medium"))
            elif last_seg.rsplit(":", 1)[1].lower() == "latest":
                out.append(Signal("docker-latest-tag", "Reliability", "Medium", fi.rel, ln,
                                  "FROM " + args, "high"))
        if m:
            stages.add(m.group(1).lower())
    return out


def rule_docker_no_healthcheck(fi: FileInfo, text: str, orchestrated: bool) -> List[Signal]:
    """Service image (EXPOSEs a port) with no HEALTHCHECK, in a repo without k8s workload YAML
    or a Compose `healthcheck:` (`orchestrated` is true when either exists).

    False-positive risks: health checking done by other orchestrators (ECS, Nomad, Cloud Run) is
    not seen; k8s ignores HEALTHCHECK, so any k8s workload YAML in the scan skips the rule, as
    does any docker-compose/compose file with a healthcheck. Images without EXPOSE (build or job
    images) are skipped. `HEALTHCHECK NONE` is an explicit choice and is not flagged. Always low
    confidence.
    """
    if orchestrated:
        return []
    ins = _instructions(text)
    if any(name == "HEALTHCHECK" for _, name, _ in ins):
        return []
    froms = [k for k, (_, name, _) in enumerate(ins) if name == "FROM"]
    final = ins[froms[-1]:] if froms else ins
    expose = [ln for ln, name, _ in final if name == "EXPOSE"]
    if not expose:
        return []
    return [Signal("docker-no-healthcheck", "Observability", "Low", fi.rel, expose[0],
                   "EXPOSE without HEALTHCHECK", "low")]


# ---------------------------------------------------------------- k8s YAML
_KIND_RE = re.compile(r"^kind:[ \t]*[\"']?(\w+)", re.M)
_CONTAINERS_RE = re.compile(r"^([ \t]*)containers:[ \t]*$")
_ITEM_RE = re.compile(r"^([ \t]*)-[ \t]+(.*)$")
_NAME_RE = re.compile(r"(?:^|[ \t])name:[ \t]*[\"']?([\w.-]+)")
_PROBE_RE = re.compile(r"^[ \t-]*(?:readinessProbe|livenessProbe):", re.M)
_PORT_RE = re.compile(r"^[ \t-]*(?:ports:|containerPort:)", re.M)
_LIMITS_RE = re.compile(r"^[ \t-]*limits:", re.M)
K8S_KINDS = ("Deployment", "StatefulSet")
_KIND_COMMENT_RE = re.compile(
    r"(?m)^[ \t]*kind:[ \t]*[\"']?(?:Deployment|StatefulSet|DaemonSet)[\"']?[ \t]+#")


def _indent(ln: str) -> int:
    return len(ln) - len(ln.lstrip(" \t"))


def _containers(text: str) -> List[Tuple[int, str, str]]:
    """(line, name, block text) for each container of each Deployment/StatefulSet document.

    YAML is read by indentation: list items under a `containers:` key; initContainers are skipped.
    """
    out: List[Tuple[int, str, str]] = []
    lines = text.split("\n")
    doc_start = 0
    bounds = [i for i, ln in enumerate(lines) if ln.startswith("---")] + [len(lines)]
    for end in bounds:
        doc = lines[doc_start:end]
        kind = _KIND_RE.search("\n".join(doc))
        if kind and kind.group(1) in K8S_KINDS:
            i = 0
            while i < len(doc):
                m = _CONTAINERS_RE.match(doc[i])
                if not m:
                    i += 1
                    continue
                base = len(m.group(1))
                j = i + 1
                item: Optional[Tuple[int, int]] = None  # (start index, item indent)
                items: List[Tuple[int, int]] = []
                while j < len(doc):
                    ln = doc[j]
                    if ln.strip():
                        ind = _indent(ln)
                        im = _ITEM_RE.match(ln)
                        if ind < base or (ind == base and not im):
                            break
                        if im and (item is None or ind == item[1]):
                            item = (j, ind)
                            items.append((j, j))
                    if items:
                        items[-1] = (items[-1][0], j)
                    j += 1
                for start, stop in items:
                    block = "\n".join(doc[start:stop + 1])
                    nm = _NAME_RE.search(block)
                    out.append((doc_start + start + 1, nm.group(1) if nm else "?", block))
                i = j
        doc_start = end + 1
    return out


def rule_k8s_no_probes(fi: FileInfo, text: str) -> List[Signal]:
    """Deployment/StatefulSet container that declares ports but has no readiness or liveness probe.

    False-positive risks: probes injected by Helm values, Kustomize patches or a mutating webhook
    are not seen (Helm templates containing `{{` are skipped). Containers without ports
    (workers, sidecars) are skipped, so a hung worker without a liveness probe is missed.
    """
    out = []
    for line, name, block in _containers(text):
        if _PORT_RE.search(block) and not _PROBE_RE.search(block):
            out.append(Signal("k8s-no-probes", "Reliability", "Medium", fi.rel, line,
                              "container %s has no readiness/liveness probe" % name, "medium"))
    return out


def rule_k8s_no_limits(fi: FileInfo, text: str, has_limit_range: bool) -> List[Signal]:
    """Deployment/StatefulSet container with no resources.limits.

    False-positive risks: limits applied by a namespace LimitRange outside the scan, Kustomize
    patches or Helm values are not seen (a LimitRange anywhere in the scan skips the rule; Helm
    templates containing `{{` are skipped). Any `limits:` key counts (CPU or memory).
    """
    if has_limit_range:
        return []
    out = []
    for line, name, block in _containers(text):
        if not _LIMITS_RE.search(block):
            out.append(Signal("k8s-no-limits", "Scalability", "Medium", fi.rel, line,
                              "container %s has no resource limits" % name, "medium"))
    return out


# ---------------------------------------------------------------- CI configs
# Anything that looks like it could run tests counts (the rule fires only on absence).
_TEST_RE = re.compile(
    r"\btests?\b|\btest[:_-]|pytest|unittest|\bnose2?\b|\btox\b|\bnox\b|\bjest\b|vitest|mocha"
    r"|\bava\b|jasmine|karma|cypress|playwright|\be2e\b|rspec|\bspec\b|phpunit|\bpest\b"
    r"|\bctest\b|\bcheck\b|\bverify\b|\bgradlew?\b[^\n]*\b(?:build|check)\b"
    r"|\bmvnw?\b[^\n]*\b(?:package|install|verify)\b|\bmake\b|\brun[ \t]+ci\b|scripts/ci\b", re.I)
_REUSABLE_RE = re.compile(r"^[ \t]+uses:[ \t]*[\"']?(?:\./|[\w.-]+/[\w.-]+/\.github/workflows/)", re.M)
_INCLUDE_RE = re.compile(r"^(?:include|extends):|^orbs:", re.M)


def _ci_runs_tests(text: str) -> Optional[bool]:
    """True/False, or None when the config delegates to something unseen (reusable workflows,
    local actions, GitLab include, CircleCI orbs)."""
    if _REUSABLE_RE.search(text) or _INCLUDE_RE.search(text):
        return None
    return bool(_TEST_RE.search(text))


def rule_ci_no_test_step(ci: List[Tuple[FileInfo, str]]) -> List[Signal]:
    """CI system (all GitHub workflows together; GitLab CI; CircleCI) with no step that runs tests.

    False-positive risks: tests run by a script whose name and path do not mention test/check/ci
    (./build.sh) are not seen. Release, deploy and housekeeping workflows are fine as long as any
    workflow of the same system runs tests. Configs that delegate (reusable workflows, local
    actions `uses: ./`, GitLab include/extends, CircleCI orbs) are skipped. One signal per system,
    on its first file.
    """
    systems: Dict[str, List[Tuple[FileInfo, str]]] = {}
    for fi, text in ci:
        key = "github" if "/.github/workflows/" in "/" + fi.rel else (
            "gitlab" if fi.rel.endswith(".gitlab-ci.yml") else "circleci")
        systems.setdefault(key + ":" + _ci_root(fi.rel), []).append((fi, text))
    out = []
    for key in sorted(systems):
        verdicts = [_ci_runs_tests(text) for _, text in systems[key]]
        if any(v is None or v for v in verdicts):
            continue
        fi = min((f for f, _ in systems[key]), key=lambda f: f.rel)
        out.append(Signal("ci-no-test-step", "Maintainability", "Medium", fi.rel, 1,
                          "no CI step runs tests (%s)" % key.split(":")[0], "medium"))
    return out


def _ci_root(rel: str) -> str:
    for marker in ("/.github/workflows/", "/.circleci/"):
        k = ("/" + rel).find(marker)
        if k >= 0:
            return ("/" + rel)[:k] or "."
    return posixpath.dirname(rel) or "."


# ---------------------------------------------------------------- runner
def run_infra_rules(files: List[FileInfo], cache: Optional[FileCache] = None) -> List[Signal]:
    """Run every infra rule over the is_infra files (test and example folders skipped).
    `cache` is the scan's file cache (files are read directly without one)."""
    read = cache.text if cache is not None else _read
    infra = [f for f in files if f.is_infra and not f.is_test and not f.is_example]
    # k8s workload YAML whose `kind:` line carries a trailing comment (`kind: Deployment # web`)
    # may be walked as plain config; pick it up by content here.
    infra += [f for f in files if not f.is_infra and f.is_config and not f.is_test
              and not f.is_example and f.ext in (".yml", ".yaml")
              and _KIND_COMMENT_RE.search(read(f))]
    if not infra:
        return []
    texts = {f.rel: read(f).replace("\r\n", "\n") for f in infra}
    k8s = [f for f in infra if not _is_dockerfile(f) and not _is_ci(f)]
    has_limit_range = any(
        re.search(r"(?m)^kind:[ \t]*[\"']?LimitRange\b", read(f))
        for f in files if f.is_config and f.ext in (".yml", ".yaml") and not f.is_test)
    compose_health = any(
        re.search(r"(?m)^[ \t]+healthcheck:", read(f)) for f in files
        if re.match(r"(?:docker-)?compose(?:\.[\w-]+)?\.ya?ml$", posixpath.basename(f.rel)))
    out: List[Signal] = []
    ci: List[Tuple[FileInfo, str]] = []
    for f in infra:
        if _is_dockerfile(f):
            text = _docker_text(texts[f.rel])
            out += rule_docker_root_user(f, text)
            out += rule_docker_latest_tag(f, text)
            out += rule_docker_no_healthcheck(f, text, bool(k8s) or compose_health)
        elif _is_ci(f):
            ci.append((f, _yaml_text(texts[f.rel])))
        elif "{{" not in texts[f.rel]:  # Helm templates: values are not visible
            text = _yaml_text(texts[f.rel])
            out += rule_k8s_no_probes(f, text)
            out += rule_k8s_no_limits(f, text, has_limit_range)
    out += rule_ci_no_test_step(ci)
    return out


# ---------------------------------------------------------------- registry
_DOCKER = ["Dockerfile"]
_K8S = ["Kubernetes YAML"]
for _m in [
    RuleMeta("docker-root-user", "2", "Security", "High", "medium",
             "Dockerfile whose final stage has no USER instruction or switches back to root.",
             "Only the final stage is checked; a final stage built FROM an earlier stage inherits "
             "that stage's USER. Non-root base images are recognised by name only "
             "(nonroot, rootless, unprivileged, cgr.dev/, bitnami/); a user set at run time "
             "(docker run --user, k8s securityContext) is not seen. Variable USER values count as "
             "non-root. An explicit `USER root` is reported at medium confidence (the rule ceiling).",
             "Create an unprivileged user and add `USER <name>` to the final stage.", _DOCKER),
    RuleMeta("docker-latest-tag", "1", "Reliability", "Medium", "medium",
             "FROM with the :latest tag (medium confidence) or no tag at all (also medium).",
             "Stage references, scratch, ARG-provided images and digest-pinned images are skipped; "
             "line continuations are not joined; images built elsewhere (bake files, compose "
             "build args) are not seen.",
             "Pin base images to a version tag (and ideally a digest).", _DOCKER),
    RuleMeta("docker-no-healthcheck", "2", "Observability", "Low", "low",
             "Dockerfile that EXPOSEs a port but declares no HEALTHCHECK.",
             "Skipped when any k8s workload YAML is in the scan (k8s ignores HEALTHCHECK and uses "
             "probes), when a compose file declares a healthcheck, and for images without "
             "EXPOSE. Other orchestrator health checks (ECS, Nomad, Cloud Run) are not seen. "
             "Always low confidence.",
             "Add a HEALTHCHECK or rely on orchestrator health probes.", _DOCKER),
    RuleMeta("k8s-no-probes", "2", "Reliability", "Medium", "medium",
             "Deployment/StatefulSet container that declares ports but has no readiness or "
             "liveness probe.",
             "YAML is read by indentation, not parsed. Containers without ports, DaemonSets, "
             "Helm templates (files containing `{{`) and probes added by Kustomize patches or "
             "webhooks are not seen.",
             "Add readinessProbe and livenessProbe to every serving container.", _K8S),
    RuleMeta("k8s-no-limits", "2", "Scalability", "Medium", "medium",
             "Deployment/StatefulSet container with no resources.limits.",
             "A LimitRange anywhere in the scan skips the rule; namespace defaults set outside the "
             "repo, Kustomize patches and Helm values are not seen (Helm templates are skipped). "
             "DaemonSets are not checked. Any limits key counts.",
             "Set resources.requests and resources.limits (at least memory).", _K8S),
    RuleMeta("ci-no-test-step", "1", "Maintainability", "Medium", "medium",
             "CI system (GitHub Actions workflows together, GitLab CI, CircleCI) where no job "
             "runs tests.",
             "Any test-like word (test, pytest, jest, check, verify, make, `run ci`, ...) counts as a "
             "test step, so weak or skipped test steps are not detected; tests hidden in a "
             "generically named script are missed (flagged). Configs that delegate to reusable "
             "workflows, local actions, GitLab include/extends or CircleCI orbs are skipped. Other "
             "CI systems (Jenkins, Azure Pipelines, Buildkite) are not read.",
             "Run the test suite on every push and pull request.", ["GitHub Actions", "GitLab CI",
                                                                    "CircleCI"]),
]:
    register(_m)
