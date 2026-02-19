#!/usr/bin/env python3
"""
validate-yaml.py ( https://github.com/jrelo/k8s.101 )

Validates Kubernetes YAML files exported from k8s.101.
Checks structural correctness, required fields, type safety, and common
misconfiguration patterns that kubectl would either reject or silently
misinterpret.

Usage:
    python3 validate-yaml.py <file.yaml> [file2.yaml ...]
    python3 validate-yaml.py *.yaml

"""

import sys
import os
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class Failure(Exception):
    pass


def assert_field(obj, path, expected_type=None, required=True):
    """
    Walk a dot-separated path into obj and optionally check the type.
    Raises Failure with a descriptive message on any problem.
    """
    parts = path.split(".")
    cur = obj
    for part in parts:
        if not isinstance(cur, dict) or part not in cur:
            if required:
                raise Failure(f"missing required field: {path}")
            return None
        cur = cur[part]
    if expected_type is not None and not isinstance(cur, expected_type):
        raise Failure(
            f"{path} should be {expected_type.__name__}, "
            f"got {type(cur).__name__}: {cur!r}"
        )
    return cur


def check_string_not_coerced(value, path):
    """
    Flag values that look like they survived YAML type coercion when they
    should have been quoted strings. e.g. version: 2.4 parsed as float 2.4.
    """
    if isinstance(value, (int, float, bool)) and value is not True and value is not False:
        raise Failure(
            f"{path} parsed as {type(value).__name__} ({value!r}) -- "
            f"if this should be a string it needs quoting in the YAML source"
        )


def check_no_duplicate_names(items, key, context):
    seen = {}
    for item in items:
        name = item.get(key)
        if name in seen:
            raise Failure(f"duplicate {key}: {name!r} in {context}")
        seen[name] = True


# ---------------------------------------------------------------------------
# Per-kind validators
# ---------------------------------------------------------------------------

def validate_container(c, path, pod_spec=None):
    results = []

    name = assert_field(c, "name", str)
    assert_field(c, "image", str)

    image = c.get("image", "")
    if image.endswith(":latest"):
        results.append(("WARN", f"{path}.image uses :latest tag"))
    if image == "" or ":" not in image:
        results.append(("WARN", f"{path}.image has no explicit tag"))

    pull = c.get("imagePullPolicy", "")
    if image.endswith(":latest") and pull != "Always":
        results.append((
            "WARN",
            f"{path} uses :latest but imagePullPolicy is {pull!r} -- "
            "cached stale image risk"
        ))

    resources = c.get("resources", {})
    if not resources:
        results.append(("WARN", f"{path} has no resource requests or limits"))
    else:
        requests = resources.get("requests", {})
        limits   = resources.get("limits", {})
        if not requests.get("cpu"):
            results.append(("WARN", f"{path}.resources.requests.cpu not set"))
        if not requests.get("memory"):
            results.append(("WARN", f"{path}.resources.requests.memory not set"))
        if not limits.get("cpu"):
            results.append(("WARN", f"{path}.resources.limits.cpu not set"))
        if not limits.get("memory"):
            results.append(("WARN", f"{path}.resources.limits.memory not set"))

    ports = c.get("ports", [])
    if ports:
        check_no_duplicate_names(ports, "containerPort", f"{path}.ports")
        for i, p in enumerate(ports):
            port_num = p.get("containerPort")
            if not isinstance(port_num, int):
                raise Failure(
                    f"{path}.ports[{i}].containerPort should be int, "
                    f"got {type(port_num).__name__}: {port_num!r}"
                )
            if not (1 <= port_num <= 65535):
                raise Failure(f"{path}.ports[{i}].containerPort {port_num} out of range")

    env = c.get("env", [])
    env_from = c.get("envFrom", [])
    for i, e in enumerate(env):
        if "name" not in e:
            raise Failure(f"{path}.env[{i}] missing name")
        # valueFrom and value are both valid; envFrom on the container is also valid
        if "value" not in e and "valueFrom" not in e and not env_from:
            results.append(("WARN", f"{path}.env[{i}] ({e.get('name')!r}) has neither value nor valueFrom"))
        val = e.get("value")
        if val is not None and not isinstance(val, str):
            raise Failure(
                f"{path}.env[{i}] ({e.get('name')!r}).value parsed as "
                f"{type(val).__name__}: {val!r} -- should be a quoted string"
            )

    liveness = c.get("livenessProbe")
    readiness = c.get("readinessProbe")
    startup   = c.get("startupProbe")
    if readiness and not liveness:
        results.append(("WARN", f"{path} has readinessProbe but no livenessProbe"))
    for probe_name, probe in [("livenessProbe", liveness), ("readinessProbe", readiness), ("startupProbe", startup)]:
        if probe:
            delay = probe.get("initialDelaySeconds", 0)
            if not isinstance(delay, int):
                raise Failure(f"{path}.{probe_name}.initialDelaySeconds should be int, got {type(delay).__name__}")

    return results


def validate_pod_spec(spec, indent_path):
    results = []

    containers = assert_field(spec, "containers", list)
    if not containers:
        raise Failure(f"{indent_path}.containers is empty")

    check_no_duplicate_names(containers, "name", f"{indent_path}.containers")

    for i, c in enumerate(containers):
        results += validate_container(c, f"{indent_path}.containers[{i}] ({c.get('name', '?')})", spec)

    init_containers = spec.get("initContainers", [])
    for i, c in enumerate(init_containers):
        results += validate_container(c, f"{indent_path}.initContainers[{i}] ({c.get('name', '?')})", spec)

    volumes = spec.get("volumes", [])
    check_no_duplicate_names(volumes, "name", f"{indent_path}.volumes")

    # cross-check volumeMounts reference declared volumes.
    # we collect all declared volume names regardless of type.
    vol_names = {v.get("name") for v in volumes if v.get("name")}
    all_containers = containers + init_containers
    for c in all_containers:
        for vm in c.get("volumeMounts", []):
            ref = vm.get("name")
            if ref and ref not in vol_names:
                raise Failure(
                    f"container {c.get('name')!r} mounts volume {ref!r} "
                    f"but it is not declared in spec.volumes"
                )

    sa = spec.get("serviceAccountName", "")
    if sa == "default":
        results.append(("WARN", f"{indent_path}.serviceAccountName is 'default' -- consider a dedicated SA"))

    return results


def validate_deployment(doc):
    results = []

    assert_field(doc, "metadata.name", str)
    assert_field(doc, "metadata.namespace", str)
    assert_field(doc, "spec.replicas", int)
    assert_field(doc, "spec.selector.matchLabels", dict)
    assert_field(doc, "spec.template.metadata.labels", dict)

    replicas = doc["spec"]["replicas"]
    if replicas < 1:
        raise Failure(f"spec.replicas is {replicas} -- must be >= 1")
    if replicas == 1:
        results.append(("WARN", "spec.replicas is 1 -- no redundancy"))

    match_labels = doc["spec"]["selector"]["matchLabels"]
    pod_labels   = doc["spec"]["template"]["metadata"]["labels"]
    for k, v in match_labels.items():
        if pod_labels.get(k) != v:
            raise Failure(
                f"selector.matchLabels[{k!r}]={v!r} not present in "
                f"template.metadata.labels (selector would match nothing)"
            )

    # check label values that could have been coerced
    for label_path, label_dict in [
        ("metadata.labels",               doc.get("metadata", {}).get("labels", {})),
        ("spec.template.metadata.labels", pod_labels),
        ("spec.selector.matchLabels",     match_labels),
    ]:
        for k, v in label_dict.items():
            check_string_not_coerced(v, f"{label_path}[{k!r}]")

    # annotation value type checks
    for k, v in doc.get("metadata", {}).get("annotations", {}).items():
        if not isinstance(v, str):
            raise Failure(
                f"metadata.annotations[{k!r}] parsed as {type(v).__name__}: {v!r} "
                f"-- annotation values must be quoted strings"
            )

    strategy = doc["spec"].get("strategy", {}).get("type", "RollingUpdate")
    if strategy == "RollingUpdate":
        ru = doc["spec"].get("strategy", {}).get("rollingUpdate", {})
        for key in ("maxSurge", "maxUnavailable"):
            val = ru.get(key)
            if val is None:
                results.append(("WARN", f"spec.strategy.rollingUpdate.{key} not set, using cluster default"))
            elif not isinstance(val, (int, str)):
                raise Failure(f"spec.strategy.rollingUpdate.{key} unexpected type: {type(val).__name__}")

    results += validate_pod_spec(
        doc["spec"]["template"]["spec"],
        "spec.template.spec"
    )

    return results


def validate_service(doc):
    results = []

    assert_field(doc, "metadata.name", str)
    assert_field(doc, "metadata.namespace", str)
    assert_field(doc, "spec.type", str)
    assert_field(doc, "spec.selector", dict)

    svc_type = doc["spec"]["type"]
    if svc_type not in ("ClusterIP", "NodePort", "LoadBalancer", "ExternalName", "Headless"):
        results.append(("WARN", f"spec.type {svc_type!r} is non-standard"))

    ports = assert_field(doc, "spec.ports", list)
    if not ports:
        raise Failure("spec.ports is empty")
    check_no_duplicate_names(ports, "name", "spec.ports")
    for i, p in enumerate(ports):
        port = p.get("port")
        if not isinstance(port, int):
            raise Failure(f"spec.ports[{i}].port should be int, got {type(port).__name__}: {port!r}")
        target = p.get("targetPort")
        if target is not None and not isinstance(target, (int, str)):
            raise Failure(f"spec.ports[{i}].targetPort unexpected type: {type(target).__name__}")

    return results


def validate_ingress(doc):
    results = []

    assert_field(doc, "metadata.name", str)
    assert_field(doc, "metadata.namespace", str)

    rules = assert_field(doc, "spec.rules", list)
    if not rules:
        raise Failure("spec.rules is empty")

    for i, rule in enumerate(rules):
        host = rule.get("host")
        if not host:
            results.append(("WARN", f"spec.rules[{i}] has no host -- matches all hosts"))
        paths = rule.get("http", {}).get("paths", [])
        if not paths:
            raise Failure(f"spec.rules[{i}].http.paths is empty")
        for j, path in enumerate(paths):
            assert_field(path, "path", str)
            svc_name = path.get("backend", {}).get("service", {}).get("name")
            svc_port = path.get("backend", {}).get("service", {}).get("port", {}).get("number")
            if not svc_name:
                raise Failure(f"spec.rules[{i}].http.paths[{j}].backend.service.name missing")
            if svc_port is not None and not isinstance(svc_port, int):
                raise Failure(
                    f"spec.rules[{i}].http.paths[{j}].backend.service.port.number "
                    f"should be int, got {type(svc_port).__name__}: {svc_port!r}"
                )

    tls = doc.get("spec", {}).get("tls", [])
    for i, entry in enumerate(tls):
        if not entry.get("secretName"):
            results.append(("WARN", f"spec.tls[{i}] has no secretName"))

    return results


def validate_configmap(doc):
    results = []

    assert_field(doc, "metadata.name", str)
    assert_field(doc, "metadata.namespace", str)

    data = doc.get("data", {})
    if not data:
        results.append(("WARN", "data is empty"))
    for k, v in data.items():
        if not isinstance(v, str):
            raise Failure(
                f"data[{k!r}] parsed as {type(v).__name__}: {v!r} "
                f"-- ConfigMap values must be strings"
            )

    return results


def validate_cronjob(doc):
    results = []

    assert_field(doc, "metadata.name", str)
    assert_field(doc, "metadata.namespace", str)

    schedule = assert_field(doc, "spec.schedule", str)
    # basic cron field count check
    parts = schedule.strip().split()
    if len(parts) != 5:
        raise Failure(
            f"spec.schedule {schedule!r} has {len(parts)} fields, expected 5"
        )

    concurrency = doc.get("spec", {}).get("concurrencyPolicy", "Allow")
    if concurrency not in ("Allow", "Forbid", "Replace"):
        raise Failure(f"spec.concurrencyPolicy {concurrency!r} is not valid")

    pod_spec_path = "spec.jobTemplate.spec.template.spec"
    try:
        pod_spec = (doc["spec"]["jobTemplate"]["spec"]["template"]["spec"])
    except KeyError as e:
        raise Failure(f"missing {pod_spec_path}: {e}")

    restart = pod_spec.get("restartPolicy", "")
    if restart not in ("OnFailure", "Never"):
        raise Failure(
            f"{pod_spec_path}.restartPolicy is {restart!r} -- "
            "CronJob pods must use OnFailure or Never"
        )

    results += validate_pod_spec(pod_spec, pod_spec_path)

    return results


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

VALIDATORS = {
    "Deployment": validate_deployment,
    "Service":    validate_service,
    "Ingress":    validate_ingress,
    "ConfigMap":  validate_configmap,
    "CronJob":    validate_cronjob,
}

REQUIRED_API_VERSIONS = {
    "Deployment": "apps/v1",
    "Service":    "v1",
    "ConfigMap":  "v1",
    "Ingress":    "networking.k8s.io/v1",
    "CronJob":    "batch/v1",
}


# Volume source keys that are valid but not modeled by k8s.101.
# We recognize them so volumeMount cross-checks don't false-positive.
KNOWN_VOLUME_TYPES = {
    "emptyDir", "configMap", "secret", "persistentVolumeClaim",
    "hostPath", "projected", "csi", "nfs", "iscsi", "glusterfs",
    "rbd", "fc", "azureDisk", "azureFile", "gcePersistentDisk",
    "awsElasticBlockStore", "downwardAPI", "ephemeral",
}

# Kinds we skip with a notice rather than erroring.
SKIP_KINDS = {
    "Namespace", "ServiceAccount", "Role", "ClusterRole",
    "RoleBinding", "ClusterRoleBinding", "NetworkPolicy",
    "PodDisruptionBudget", "HorizontalPodAutoscaler",
    "VerticalPodAutoscaler", "PodSecurityPolicy",
    "StorageClass", "PersistentVolume", "PersistentVolumeClaim",
    "CustomResourceDefinition", "Secret",
}


def validate_file(path):
    """
    Returns (file_passed: bool, output_lines: list[str])

    Handles single-document and multi-document (---) YAML streams.
    Unknown kinds are skipped with a notice rather than treated as errors.
    """
    output = []

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        return False, [f"ERROR  could not read file: {e}"]

    try:
        docs = list(yaml.safe_load_all(raw))
    except yaml.YAMLError as e:
        return False, [f"ERROR  YAML parse error: {e}"]

    # filter out null documents (bare --- separators produce None)
    docs = [d for d in docs if d is not None]

    if not docs:
        return False, ["ERROR  file contains no documents"]

    multi = len(docs) > 1
    file_passed = True

    for idx, doc in enumerate(docs):
        prefix = f"[{idx+1}] " if multi else ""

        if not isinstance(doc, dict):
            output.append(f"ERROR  {prefix}document root is not a mapping")
            file_passed = False
            continue

        kind = doc.get("kind", "")
        name = doc.get("metadata", {}).get("name", "?")
        label = f"{prefix}{kind}/{name}" if kind else f"{prefix}document"

        if not kind:
            output.append(f"ERROR  {label}: missing kind")
            file_passed = False
            continue

        if kind in SKIP_KINDS:
            output.append(f"skip   {label}: kind not validated (out of scope)")
            continue

        api_version = doc.get("apiVersion", "")
        expected_api = REQUIRED_API_VERSIONS.get(kind)
        doc_messages = []

        if expected_api and api_version != expected_api:
            doc_messages.append(
                f"WARN   apiVersion is {api_version!r}, expected {expected_api!r}"
            )

        validator = VALIDATORS.get(kind)
        if not validator:
            supported = ", ".join(sorted(VALIDATORS))
            output.append(f"skip   {label}: kind not validated (supported: {supported})")
            continue

        try:
            warnings = validator(doc)
            for level, msg in warnings:
                doc_messages.append(f"{level:<6} {msg}")
            doc_passed = True
        except Failure as e:
            doc_messages.append(f"ERROR  {e}")
            doc_passed = False
            file_passed = False

        if doc_passed and not doc_messages:
            output.append(f"  ok   {label}")
        elif doc_passed:
            output.append(f"  ok   {label}")
            for m in doc_messages:
                output.append(f"       {m}")
        else:
            output.append(f"  FAIL {label}")
            for m in doc_messages:
                output.append(f"       {m}")

    return file_passed, output


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    paths = sys.argv[1:]
    total  = len(paths)
    failed = 0

    for path in paths:
        name = os.path.basename(path)
        passed, output = validate_file(path)

        print(f"{name}")
        for line in output:
            print(f"  {line}")

        if not passed:
            failed += 1
        print()

    print()
    if failed == 0:
        print(f"passed {total}/{total}")
    else:
        print(f"passed {total - failed}/{total}  ({failed} failed)")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
  
