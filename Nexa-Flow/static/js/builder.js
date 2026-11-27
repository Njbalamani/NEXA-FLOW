const { useCallback, useEffect, useMemo, useRef, useState } = React;

const root = document.getElementById("builder-root");
const workflowId = Number(root.dataset.workflowId);
const initialDefinition = JSON.parse(root.dataset.definition || "{}");
const nodeCatalog = [
  { type: "trigger", label: "Trigger", icon: "⚡", hint: "Starts the workflow" },
  { type: "condition", label: "Condition", icon: "◇", hint: "Branches on logic" },
  { type: "action", label: "Action", icon: "→", hint: "Runs an operation" },
  { type: "delay", label: "Delay", icon: "◷", hint: "Waits before continuing" },
  { type: "transform", label: "Transform", icon: "⌘", hint: "Sets context variables" },
  { type: "webhook", label: "Webhook", icon: "⇄", hint: "Calls an external URL" }
];

function uid(prefix) {
  return prefix + "_" + Math.random().toString(36).slice(2, 8);
}

function defaultConfig(type) {
  if (type === "condition") return { field: "trigger.payload.status", operator: "eq", value: "approved" };
  if (type === "action") return { action: "log", message: "Workflow step executed", retry_attempts: 1 };
  if (type === "delay") return { seconds: 5 };
  if (type === "transform") return { variables: { stage: "processed" } };
  if (type === "webhook") return { url: "https://example.com/webhook", method: "POST", body: {} };
  return {};
}

function defaultTriggerConfig(type) {
  if (type === "schedule") return { mode: "interval", minutes: 60 };
  if (type === "webhook") return { description: "Signed webhook endpoint" };
  return {};
}

function api(path, options) {
  const token = window.NexaFlow ? window.NexaFlow.csrfToken() : "";
  return fetch(path, Object.assign({
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": token,
      "X-Requested-With": "XMLHttpRequest"
    },
    credentials: "same-origin"
  }, options || {}));
}

function errorMessage(payload, fallback) {
  if (payload && typeof payload === "object") {
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.errors)) return payload.errors.join("; ");
    const values = Object.keys(payload)
      .map((key) => {
        const value = payload[key];
        if (Array.isArray(value)) return value.join(", ");
        return typeof value === "string" ? value : JSON.stringify(value);
      })
      .filter(Boolean);
    if (values.length) return values.join("; ");
  }
  return fallback;
}

function App() {
  const [nodes, setNodes] = useState(initialDefinition.nodes || []);
  const [connections, setConnections] = useState(initialDefinition.connections || []);
  const [triggers, setTriggers] = useState(initialDefinition.triggers || []);
  const [selectedKey, setSelectedKey] = useState(null);
  const [selectedTriggerId, setSelectedTriggerId] = useState(null);
  const [pending, setPending] = useState(null);
  const [validation, setValidation] = useState(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const configTextRef = useRef(null);
  const triggerConfigRef = useRef(null);

  const selectedNode = useMemo(() => nodes.find((item) => item.key === selectedKey) || null, [nodes, selectedKey]);
  const selectedTrigger = useMemo(
    () => triggers.find((item) => String(item.id || item.key) === String(selectedTriggerId)) || null,
    [triggers, selectedTriggerId]
  );

  useEffect(() => {
    if (configTextRef.current && selectedNode) {
      configTextRef.current.value = JSON.stringify(selectedNode.config || {}, null, 2);
    }
  }, [selectedNode]);

  useEffect(() => {
    if (triggerConfigRef.current && selectedTrigger) {
      triggerConfigRef.current.value = JSON.stringify(selectedTrigger.config || {}, null, 2);
    }
  }, [selectedTrigger]);

  const definition = useCallback(() => ({
    name: initialDefinition.name,
    description: initialDefinition.description,
    status: initialDefinition.status,
    nodes: nodes.map((node) => ({
      key: node.key,
      type: node.type,
      name: node.name,
      config: node.config || {},
      position: node.position || { x: 0, y: 0 }
    })),
    connections: connections.map((item) => ({
      source: item.source,
      target: item.target,
      source_handle: item.source_handle || "out",
      target_handle: item.target_handle || "in",
      label: item.label || ""
    })),
    triggers: triggers.map((item) => ({
      id: item.id,
      name: item.name,
      type: item.type,
      config: item.config || {},
      is_active: item.is_active !== false
    }))
  }), [nodes, connections, triggers]);

  const addNode = useCallback((type, position) => {
    const key = uid(type);
    const node = {
      key,
      type,
      name: type.charAt(0).toUpperCase() + type.slice(1),
      config: defaultConfig(type),
      position: position || { x: 120 + nodes.length * 30, y: 120 + nodes.length * 24 }
    };
    setNodes((prev) => prev.concat(node));
    setSelectedKey(key);
    setMessage(type + " node added.");
  }, [nodes.length]);

  const updateNode = useCallback((key, patch) => {
    setNodes((prev) => prev.map((node) => (node.key === key ? Object.assign({}, node, patch) : node)));
  }, []);

  const deleteNode = useCallback((key) => {
    setNodes((prev) => prev.filter((node) => node.key !== key));
    setConnections((prev) => prev.filter((item) => item.source !== key && item.target !== key));
    setSelectedKey(null);
    setMessage("Node removed.");
  }, []);

  const connectNodes = useCallback((source, handle, target) => {
    if (!source || !target || source === target) return;
    setConnections((prev) => {
      const exists = prev.some((item) => item.source === source && item.target === target && (item.source_handle || "out") === handle);
      if (exists) return prev;
      return prev.concat({ source, target, source_handle: handle || "out", target_handle: "in", label: "" });
    });
    setPending(null);
    setMessage("Nodes connected.");
  }, []);

  const removeConnection = useCallback((index) => {
    setConnections((prev) => prev.filter((_, position) => position !== index));
    setMessage("Connection removed.");
  }, []);

  const startDrag = useCallback((event, node) => {
    if (event.target.classList.contains("node-port")) return;
    event.preventDefault();
    setSelectedKey(node.key);
    const startX = event.clientX;
    const startY = event.clientY;
    const origin = node.position || { x: 0, y: 0 };
    const move = (moveEvent) => {
      const next = {
        x: Math.max(0, origin.x + moveEvent.clientX - startX),
        y: Math.max(0, origin.y + moveEvent.clientY - startY)
      };
      updateNode(node.key, { position: next });
    };
    const stop = () => {
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", stop);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", stop);
  }, [updateNode]);

  const dropNode = useCallback((event) => {
    event.preventDefault();
    const type = event.dataTransfer.getData("application/nexaflow-node");
    if (!type) return;
    const canvas = event.currentTarget;
    const rect = canvas.getBoundingClientRect();
    addNode(type, {
      x: Math.max(0, event.clientX - rect.left + canvas.scrollLeft - 95),
      y: Math.max(0, event.clientY - rect.top + canvas.scrollTop - 30)
    });
  }, [addNode]);

  const saveDefinition = useCallback(async () => {
    setBusy(true);
    setMessage("Saving workflow...");
    try {
      const selectedIndex = triggers.findIndex((trigger) => String(trigger.id || trigger.key) === String(selectedTriggerId));
      const response = await api("/api/v1/workflows/" + workflowId + "/definition/", {
        method: "PUT",
        body: JSON.stringify({ definition: definition() })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setMessage("Save failed: " + errorMessage(data, "Please review the workflow and try again."));
        return false;
      }
      setValidation(data.validation);
      const versionLabel = document.getElementById("builder-version");
      if (versionLabel) versionLabel.textContent = "Version " + data.version;
      setMessage(
        "Saved version " + data.version + " with " + data.nodes.length + " nodes and " + data.connections.length + " connections."
      );
      if (data.nodes) {
        setNodes((prev) => prev.map((node) => {
          const saved = data.nodes.find((item) => item.key === node.key);
          return saved ? Object.assign({}, node, { id: saved.id }) : node;
        }));
      }
      if (data.connections) setConnections(data.connections.map((item) => Object.assign({}, item)));
      if (data.triggers) {
        const savedTriggers = data.triggers.map((trigger) => {
          const key = String(trigger.id || trigger.key);
          const existing = triggers.find((item) => String(item.id || item.key) === key) || {};
          return Object.assign({}, existing, trigger);
        });
        setTriggers(savedTriggers);
        if (selectedIndex >= 0 && savedTriggers[selectedIndex]) {
          setSelectedTriggerId(savedTriggers[selectedIndex].id);
        }
      }
      return true;
    } catch (error) {
      setMessage("Save failed: " + (error && error.message ? error.message : "Network error."));
      return false;
    } finally {
      setBusy(false);
    }
  }, [definition, triggers, selectedTriggerId]);

  const validateDefinition = useCallback(async () => {
    setBusy(true);
    try {
      const response = await api("/api/v1/workflows/" + workflowId + "/validate-draft/", {
        method: "POST",
        body: JSON.stringify({ definition: definition() })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setMessage("Validation failed: " + errorMessage(data, "Please review the workflow and try again."));
        return;
      }
      setValidation(data);
      if (data.valid) {
        const warnings = (data.warnings || []).length;
        setMessage(
          "Valid · " + data.node_count + " nodes, " + data.connection_count + " connections" +
          (warnings ? " · " + warnings + " warning(s)." : ".")
        );
      } else {
        setMessage("Invalid · " + (data.errors || []).join("; "));
      }
    } catch (error) {
      setMessage("Validation failed: " + (error && error.message ? error.message : "Network error."));
    } finally {
      setBusy(false);
    }
  }, [definition]);

  const executeWorkflow = useCallback(async () => {
    const saved = await saveDefinition();
    if (!saved) return;
    setBusy(true);
    try {
      const response = await api("/api/v1/workflows/" + workflowId + "/execute/", {
        method: "POST",
        body: JSON.stringify({ input: { source: "visual-builder" } })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setMessage("Execution rejected: " + errorMessage(data, "Please review the workflow and try again."));
        return;
      }
      setMessage("Execution #" + data.execution_id + " started with status " + data.status + ".");
      if (data.execution_url) {
        window.location.href = data.execution_url;
        return;
      }
      setTimeout(function () { window.location.reload(); }, 900);
    } catch (error) {
      setMessage("Execution failed: " + (error && error.message ? error.message : "Network error."));
    } finally {
      setBusy(false);
    }
  }, [saveDefinition]);

  const addTrigger = useCallback(() => {
    const trigger = { key: uid("trigger"), name: "Manual trigger", type: "manual", config: {}, is_active: true };
    setTriggers((prev) => prev.concat(trigger));
    setSelectedTriggerId(trigger.key);
  }, []);

  const updateTrigger = useCallback((key, patch) => {
    setTriggers((prev) => prev.map((item) => {
      const id = item.id || item.key;
      return id === key ? Object.assign({}, item, patch) : item;
    }));
  }, []);

  const deleteTrigger = useCallback((key) => {
    setTriggers((prev) => prev.filter((item) => (item.id || item.key) !== key));
    setSelectedTriggerId(null);
  }, []);

  const nodesById = useMemo(() => {
    const map = {};
    nodes.forEach((node) => { map[node.key] = node; });
    return map;
  }, [nodes]);

  const nodeLabel = (key) => nodesById[key] ? nodesById[key].name : key;

  const paths = useMemo(() => connections.map((connection, index) => {
    const source = nodesById[connection.source];
    const target = nodesById[connection.target];
    if (!source || !target) return null;
    const handle = connection.source_handle || "out";
    const sourcePosition = source.position || { x: 0, y: 0 };
    const targetPosition = target.position || { x: 0, y: 0 };
    const startY = sourcePosition.y + (handle === "true" ? 64 : handle === "false" ? 92 : 48);
    const startX = sourcePosition.x + 190;
    const endX = targetPosition.x;
    const endY = targetPosition.y + 48;
    const curve = Math.max(40, Math.abs(endX - startX) / 2);
    return {
      key: connection.key || "c" + index,
      d: "M " + startX + " " + startY + " C " + (startX + curve) + " " + startY + ", " + (endX - curve) + " " + endY + ", " + endX + " " + endY,
      color: handle === "true" ? "#3ddc97" : handle === "false" ? "#ff6b7a" : "#d4af37"
    };
  }).filter(Boolean), [connections, nodesById]);

  return (
    <div className="builder-grid">
      <section className="panel palette">
        <div className="panel-head"><h3>Components</h3><span className="badge">Drag or +</span></div>
        <div className="panel-body">
          {nodeCatalog.map((item) => (
            <div
              key={item.type}
              className="palette-item"
              draggable
              onDragStart={(event) => event.dataTransfer.setData("application/nexaflow-node", item.type)}
              onDoubleClick={() => addNode(item.type)}
              title="Drag to canvas, double-click, or use +"
            >
              <span className="palette-icon">{item.icon}</span>
              <div><strong>{item.label}</strong><small>{item.hint}</small></div>
              <button
                className="palette-add"
                type="button"
                aria-label={"Add " + item.label}
                onClick={() => addNode(item.type)}
              >+</button>
            </div>
          ))}
          <button className="btn btn-ghost btn-sm palette-trigger-button" onClick={addTrigger}>+ Add trigger</button>
        </div>
      </section>

      <section className="panel canvas-panel">
        <div className="panel-head builder-toolbar">
          <h3>Workflow canvas</h3>
          <div className="builder-toolbar">
            {pending ? <button className="link-button" onClick={() => setPending(null)}>Clear link</button> : null}
            <button className="btn btn-gold btn-sm" disabled={busy} onClick={saveDefinition}>Save</button>
            <button className="btn btn-ghost btn-sm" disabled={busy} onClick={validateDefinition}>Validate</button>
            <button className="btn btn-ghost btn-sm" disabled={busy} onClick={executeWorkflow}>Run now</button>
          </div>
        </div>
        <div className="builder-canvas" onDragOver={(event) => event.preventDefault()} onDrop={dropNode}>
          <div className="canvas-stage">
            <svg className="connection-layer" viewBox="0 0 1400 900">
              <defs>
                <marker id="arrow-gold" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                  <path d="M0,0 L0,6 L7,3 z" fill="#d4af37" />
                </marker>
              </defs>
              {paths.map((path) => <path key={path.key} d={path.d} fill="none" stroke={path.color} strokeWidth="2" markerEnd="url(#arrow-gold)" />)}
            </svg>
            {nodes.map((node) => {
              const position = node.position || { x: 0, y: 0 };
              const isCondition = node.type === "condition";
              return (
                <div
                  key={node.key}
                  className={"workflow-node type-" + node.type + (selectedKey === node.key ? " selected" : "")}
                  style={{ left: position.x, top: position.y }}
                  onMouseDown={(event) => startDrag(event, node)}
                  onClick={() => {
                    if (pending && pending.source !== node.key) connectNodes(pending.source, pending.handle, node.key);
                    setSelectedKey(node.key);
                  }}
                >
                  <span className="node-port in" onClick={(event) => { event.stopPropagation(); if (pending) connectNodes(pending.source, pending.handle, node.key); }}></span>
                  <div className="node-head">
                    <span className="node-title">{node.name}</span>
                    <span className="node-type">{node.type}</span>
                  </div>
                  <div className="node-meta">
                    {node.type === "action" ? (node.config && node.config.action) || "action" : node.type === "condition" ? "true / false routing" : "click to configure"}
                  </div>
                  {isCondition ? (
                    <React.Fragment>
                      <span className="node-port true" title="true branch" onClick={(event) => { event.stopPropagation(); setPending({ source: node.key, handle: "true" }); }}></span>
                      <span className="node-port false" title="false branch" onClick={(event) => { event.stopPropagation(); setPending({ source: node.key, handle: "false" }); }}></span>
                    </React.Fragment>
                  ) : (
                    <span className="node-port out" title="connect" onClick={(event) => { event.stopPropagation(); setPending({ source: node.key, handle: "out" }); }}></span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </section>

      <section className="panel inspector">
        <div className="panel-head"><h3>Inspector</h3>{pending ? <span className="badge badge-warning">Connecting</span> : null}</div>
        <div className="panel-body inspector-body">
          {message ? <div className="alert alert-info">{message}</div> : null}
          {selectedNode ? (
            <React.Fragment>
              <div className="field">
                <label>Node name</label>
                <input value={selectedNode.name} onChange={(event) => updateNode(selectedNode.key, { name: event.target.value })} />
              </div>
              <div className="field">
                <label>Node type</label>
                <select value={selectedNode.type} onChange={(event) => updateNode(selectedNode.key, { type: event.target.value, config: defaultConfig(event.target.value) })}>
                  {nodeCatalog.map((item) => <option key={item.type} value={item.type}>{item.label}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Configuration (JSON)</label>
                <textarea ref={configTextRef} rows="9" defaultValue={JSON.stringify(selectedNode.config || {}, null, 2)} onBlur={(event) => {
                  try { updateNode(selectedNode.key, { config: JSON.parse(event.target.value) }); setMessage("Node configuration updated."); }
                  catch (error) { setMessage("Invalid JSON in node configuration."); }
                }} />
                <span className="help-text">Use {"{{trigger.payload.field}}"} to reference incoming data.</span>
              </div>
              <button className="btn btn-danger btn-sm" onClick={() => deleteNode(selectedNode.key)}>Delete node</button>
            </React.Fragment>
          ) : <div className="empty-state"><strong>Nothing selected</strong>Drag a component or select a node.</div>}

          <div className="card inspector-card">
            <div className="card-head"><h3>Triggers</h3><button className="btn btn-ghost btn-sm" onClick={addTrigger}>+</button></div>
            {triggers.length === 0 ? <p className="card-subtitle">No triggers yet.</p> : null}
            {triggers.map((trigger) => {
              const key = trigger.id || trigger.key;
              return (
                <div key={key} className="execution-row" onClick={() => setSelectedTriggerId(key)}>
                  <span>{trigger.name || trigger.type}</span>
                  <span className="badge badge-info">{trigger.type}</span>
                </div>
              );
            })}
            {selectedTrigger ? (
              <div className="inspector-trigger-editor">
                <div className="field">
                  <label>Trigger name</label>
                  <input value={selectedTrigger.name || ""} onChange={(event) => updateTrigger(selectedTrigger.id || selectedTrigger.key, { name: event.target.value })} />
                </div>
                <div className="field">
                  <label>Trigger type</label>
                  <select value={selectedTrigger.type} onChange={(event) => updateTrigger(selectedTrigger.id || selectedTrigger.key, { type: event.target.value, config: defaultTriggerConfig(event.target.value) })}>
                    <option value="manual">Manual</option>
                    <option value="schedule">Scheduled</option>
                    <option value="webhook">Webhook</option>
                    <option value="api">API</option>
                    <option value="event">Event</option>
                  </select>
                </div>
                <div className="field">
                  <label>Configuration (JSON)</label>
                  <textarea ref={triggerConfigRef} rows="5" defaultValue={JSON.stringify(selectedTrigger.config || {}, null, 2)} onBlur={(event) => {
                    try { updateTrigger(selectedTrigger.id || selectedTrigger.key, { config: JSON.parse(event.target.value) }); }
                    catch (error) { setMessage("Invalid JSON in trigger configuration."); }
                  }} />
                </div>
                <button className="btn btn-danger btn-sm" onClick={() => deleteTrigger(selectedTrigger.id || selectedTrigger.key)}>Delete trigger</button>
              </div>
            ) : null}
          </div>

          <div className="card inspector-card">
            <div className="card-head"><h3>Connections</h3><span className="badge">{connections.length}</span></div>
            {connections.length === 0 ? <p className="card-subtitle">Click an output port, then click a target node.</p> : null}
            {connections.map((connection, index) => (
              <div key={"c" + index} className="connection-row">
                <span>{nodeLabel(connection.source)} <small>{(connection.source_handle || "out")}</small> → {nodeLabel(connection.target)}</span>
                <button className="link-button danger" onClick={() => removeConnection(index)}>Remove</button>
              </div>
            ))}
          </div>

          {validation ? (
            <div className="card inspector-card">
              <div className="card-head">
                <h3>Validation</h3>
                <span className={"badge " + (validation.valid ? "badge-success" : "badge-failed")}>{validation.valid ? "Valid" : "Invalid"}</span>
              </div>
              <ul className="validation-list">
                {(validation.errors || []).map((error, index) => <li key={"e" + index}>{error}</li>)}
                {(validation.warnings || []).map((warning, index) => <li key={"w" + index}>{warning}</li>)}
              </ul>
              {!(validation.errors || []).length && !(validation.warnings || []).length ? <p className="card-subtitle">No issues found.</p> : null}
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("builder-app")).render(<App />);
