# k8s.101

A single file, browser-based Kubernetes YAML generator and editor.

Generate new YAML or load an existing config to edit. Supports Deployment, Service, ConfigMap, Ingress, and CronJob. Live preview updates as you type, with syntax highlighting in the output pane.

## Screenshots

<img width="2047" height="963" alt="image" src="https://github.com/user-attachments/assets/2498349f-37f5-4751-a875-b8f5f9befe3a" />

### Features

## Resource types

Generate and edit YAML for five Kubernetes resource kinds:

- Deployment
- Service
- ConfigMap
- Ingress
- CronJob

---

## Loading

- **Drag and drop** - drop a `.yaml` or `.yml` file anywhere on the window to load it
- **File picker** - use the `load .yaml` button in the top bar
- Detected resource kind switches the active tab and populates all recognized form fields automatically

---

## Passthrough mode

- Enable with the `preserve unknown fields` checkbox in the form panel header
- When active, the original document is kept in memory and unknown fields (initContainers, pod securityContext, topologySpreadConstraints, nodeAffinity, tolerations, Vault/Istio annotations, etc.) are merged back into the export
- Your edits win over the original where fields overlap
- Badge appears in the header when passthrough is active and a file is loaded

---

## Raw editor

- Accessible via `Raw Editor` in the sidebar tools section
- Full textarea showing the current YAML, passthrough-merged if that mode is on
- `apply` re-parses the content back into form state
- `reset to current` discards edits and syncs from form state
- Tab key inserts 2 spaces

---

## Form sections

Sidebar navigation breaks the config into focused sections:

- **Metadata** - name, namespace, resource-specific top-level fields
- **Spec** - replicas, update strategy, rolling update params, pod spec options
- **Containers** - per-container image, tag, pull policy; add and remove containers
- **Ports** - container ports per container
- **Env Vars** - key/value environment variables per container
- **Resources** - CPU and memory requests and limits per container
- **Probes** - liveness and readiness HTTP probes per container
- **Volumes** - emptyDir, configMap, secret, PVC, hostPath
- **Labels & Annotations** - metadata labels, annotations, and service ports
- **Affinity** - preferred pod anti-affinity with configurable topology key

---

## Output

- Live YAML preview updates on every field change
- Syntax highlighting in the output pane (keys, values, strings, numbers, booleans)
- `copy` button copies to clipboard
- `save .yaml` downloads the file named after the resource

## Themes

Cycle through `minima` / `ocean` / `coast` with the theme button in the top right. Feel free to open a PR with new ones!
