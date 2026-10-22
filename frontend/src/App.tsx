import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  CircleHelp,
  FileSpreadsheet,
  FolderOpen,
  Gauge,
  Layers,
  LogOut,
  Plus,
  RotateCcw,
  Save,
  Settings2,
  ShieldCheck,
  Trash2,
  Upload,
  X,
  Zap,
} from "lucide-react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  display,
  download,
  json,
  same,
  saveBlob,
  type Evaluation,
  type Field,
  type Preset,
  type Values,
} from "./api";
import "./style.css";

export default function App() {
  const [account, setAccount] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [base, setBase] = useState<Values | null>(null);
  const [fields, setFields] = useState<Field[]>([]);
  const [csv, setCsv] = useState<Values>({});
  const [manual, setManual] = useState<Values>({});
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [valid, setValid] = useState(false);
  const [tab, setTab] = useState("Vehicle editor");
  const [active, setActive] = useState("Quick adjustments");
  const [preview, setPreview] = useState<{
    changes: {
      parameter: string;
      before: unknown;
      after: unknown;
      unit: string;
    }[];
    errors: string[];
    overrides: Values;
  } | null>(null);
  const [presets, setPresets] = useState<{ id: number; name: string }[]>([]);
  const [presetName, setPresetName] = useState("");
  const [batchText, setBatchText] = useState("");
  const [batchName, setBatchName] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const baseInput = useRef<HTMLInputElement>(null);
  const csvInput = useRef<HTMLInputElement>(null);
  const presetInput = useRef<HTMLInputElement>(null);
  const batchInput = useRef<HTMLInputElement>(null);
  const modalOpen = preview !== null;
  useEffect(() => {
    if (!modalOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const focusable = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input, [tabindex="0"]',
        ) ?? [],
      );
    focusable()[0]?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreview(null);
      if (event.key !== "Tab") return;
      const elements = focusable();
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      previous?.focus();
    };
  }, [modalOpen]);
  const overrides = { ...csv, ...manual };
  const build = { base, overrides };
  const run = async (work: () => Promise<void>) => {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    json<{ username: string }>("/auth/me")
      .then((u) => setAccount(u.username))
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);
  const refreshPresets = useCallback(
    async () => setPresets(await json("/presets")),
    [],
  );
  useEffect(() => {
    if (!account) return;
    let alive = true;
    json<{ base: Values; fields: Field[] }>("/base")
      .then((d) => {
        if (alive) {
          setBase(d.base);
          setFields(d.fields);
        }
      })
      .catch((e) => setError(String(e)));
    refreshPresets().catch((e) => setError(String(e)));
    return () => {
      alive = false;
    };
  }, [account, refreshPresets]);
  const serialized = JSON.stringify(build);
  useEffect(() => {
    if (!base) return;
    setValid(false);
    let alive = true;
    const timer = setTimeout(() => {
      json<Evaluation>("/evaluate", JSON.parse(serialized))
        .then((data) => {
          if (alive) {
            setEvaluation(data);
            setValid(true);
            setError("");
          }
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    }, 200);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [serialized, base]);
  const replaceBase = (next: Values) => {
    setBase(next);
    setCsv({});
    setManual({});
    setPreview(null);
    setEvaluation(null);
    setNotice("Base car loaded.");
  };
  const applyPreset = (p: Preset) => {
    setBase(p.base);
    setCsv(p.csv);
    setManual(p.manual);
    setPreview(null);
    setTab("Vehicle editor");
    setNotice("Preset loaded.");
  };
  const preset = (): Preset => ({ version: 1, base: base!, csv, manual });
  const upload = async (file: File | undefined, kind: string) => {
    if (!file) return;
    if (file.size > 2_000_000) throw new Error("File exceeds 2 MB");
    if (kind === "base") {
      const form = new FormData();
      form.append("file", file);
      replaceBase((await json<{ base: Values }>("/base", form)).base);
    } else if (kind === "csv") {
      setPreview(
        await json("/csv/preview", {
          base,
          overrides: csv,
          text: await file.text(),
        }),
      );
    } else if (kind === "batch") {
      setBatchText(await file.text());
      setBatchName(file.name);
    } else {
      applyPreset(
        await json("/presets/validate", JSON.parse(await file.text())),
      );
    }
  };
  const fileInput = (
    ref: React.RefObject<HTMLInputElement | null>,
    kind: string,
    accept: string,
  ) => (
    <input
      ref={ref}
      type="file"
      accept={accept}
      hidden
      onChange={(e) => {
        const file = e.target.files?.[0];
        e.target.value = "";
        void run(() => upload(file, kind));
      }}
    />
  );
  const groups = [...new Set(fields.map((f) => f.group))];
  const changed = fields.filter(
    (f) => !same(evaluation?.car[f.key], base?.[f.key]) && f.key in overrides,
  ).length;
  const setField = (key: string, value: unknown) => {
    setValid(false);
    setManual((old) => ({ ...old, [key]: value }));
  };
  const reset = (key: string) => {
    setValid(false);
    if (key in manual)
      setManual((old) => {
        const n = { ...old };
        delete n[key];
        return n;
      });
    else
      setCsv((old) => {
        const n = { ...old };
        delete n[key];
        return n;
      });
  };
  if (checking)
    return (
      <div className="loading">
        <Activity /> Loading car builder…
      </div>
    );
  if (!account)
    return (
      <div className="auth-shell">
        <div className="auth-story">
          <div className="brand">
            <span className="brand-mark">
              RFR<span>27</span>
            </span>
            <span>ENGINEERING TOOLS</span>
          </div>
          <div>
            <div className="eyebrow">FROM PARAMETERS TO THE STARTING LINE</div>
            <h1>
              Your next car.
              <br />
              Built in seconds.
            </h1>
            <p>
              Turn vehicle parameters into OptimumLap files. Refine the setup,
              see the curve, and get back to engineering.
            </p>
            <div className="auth-features">
              <span>
                <Gauge /> Live power curves
              </span>
              <span>
                <Layers /> Traceable changes
              </span>
              <span>
                <FileSpreadsheet /> Batch generation
              </span>
            </div>
          </div>
          <small>RUTGERS FORMULA RACING · VEHICLE PERFORMANCE</small>
        </div>
        <div className="auth-form">
          <div className="mini-brand">
            <Activity /> CAR BUILDER
          </div>
          <h2>{register ? "Create your account" : "Welcome back"}</h2>
          <p>
            {register
              ? "Save your setups and build your next iteration."
              : "Sign in to your vehicle workspace."}
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                const u = await json<{ username: string }>(
                  register ? "/auth/register" : "/auth/login",
                  { username, password },
                );
                setAccount(u.username);
                setPassword("");
              });
            }}
          >
            <label>
              Username
              <input
                autoComplete="username"
                pattern="[a-zA-Z0-9_-]+"
                minLength={3}
                maxLength={40}
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </label>
            <label>
              Password
              <input
                type="password"
                autoComplete={register ? "new-password" : "current-password"}
                minLength={10}
                maxLength={128}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            {register && (
              <small>
                At least 10 characters. Password recovery is managed by your
                administrator.
              </small>
            )}
            {error && (
              <div className="message error" role="alert">
                {error}
              </div>
            )}
            <button className="primary full" disabled={busy}>
              {busy ? "Working…" : register ? "Create account" : "Sign in"}
              <ArrowRight size={17} />
            </button>
          </form>
          <button
            className="text-button"
            onClick={() => {
              setRegister(!register);
              setError("");
            }}
          >
            {register
              ? "Already have an account? Sign in"
              : "New to the team? Create an account"}
          </button>
        </div>
      </div>
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Car builder home">
          <span className="brand-mark">
            RFR<span>27</span>
          </span>
          <span>ENGINEERING TOOLS</span>
        </a>
        <div className="workspace-label">WORKSPACE</div>
        {[
          ["Vehicle editor", Settings2],
          ["Batch export", Layers],
          ["Saved presets", FolderOpen],
        ].map(([name, Icon]) => (
          <button
            key={String(name)}
            className={`nav-item ${tab === name ? "selected" : ""}`}
            aria-pressed={tab === name}
            onClick={() => setTab(String(name))}
          >
            {typeof Icon !== "string" && <Icon size={18} />}
            <span>{String(name)}</span>
            {name === "Saved presets" && <b>{presets.length}</b>}
          </button>
        ))}
        <div className="sidebar-note">
          <span className="status-dot" /> OPTIMUMLAP READY
          <p>
            One workspace.
            <br />
            Every iteration.
          </p>
          <small>.OLVeh vehicle file generator</small>
        </div>
        <button
          className="profile"
          aria-label="Change password"
          onClick={() => setShowPassword(!showPassword)}
        >
          <span className="avatar">{account.slice(0, 2).toUpperCase()}</span>
          <span>
            {account}
            <small>Team member</small>
          </span>
          <ChevronDown size={15} />
        </button>
        <button
          className="logout"
          onClick={() =>
            void run(async () => {
              await api("/auth/logout", {});
              setAccount(null);
              setBase(null);
              setCsv({});
              setManual({});
              setEvaluation(null);
            })
          }
        >
          <LogOut size={14} /> Sign out
        </button>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span>
            Vehicle performance <span className="slash">/</span>{" "}
            <b>Car builder</b>
          </span>
          <span className="version">
            <span className="status-dot" /> Workspace online
          </span>
        </header>
        <main>
          <div className="page-title">
            <div>
              <div className="eyebrow">RFR ENGINEERING / OPTIMUMLAP</div>
              <h1>{tab === "Vehicle editor" ? "Vehicle builder" : tab}</h1>
              <p>
                {tab === "Vehicle editor"
                  ? "A better setup starts with the right numbers."
                  : tab === "Batch export"
                    ? "Explore more setups. Export every iteration together."
                    : "Keep your best configurations ready for the next session."}
              </p>
            </div>
            {tab === "Vehicle editor" && (
              <button
                className="primary"
                disabled={!valid || busy}
                onClick={() =>
                  void run(async () => {
                    await download("/export", build);
                    setNotice("Vehicle exported. Ready for OptimumLap.");
                  })
                }
              >
                <ArrowDownToLine size={17} /> Export .OLVeh
              </button>
            )}
          </div>
          {error && (
            <div className="message error" role="alert">
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="message success" role="status">
              <Check size={16} />
              {notice}
              <button aria-label="Dismiss notice" onClick={() => setNotice("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {showPassword && (
            <section className="panel">
              <div className="panel-heading">
                <h2>Change password</h2>
                <button
                  onClick={() => setShowPassword(false)}
                  aria-label="Close password form"
                >
                  <X size={16} />
                </button>
              </div>
              <form
                className="inline-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    await json("/auth/password", {
                      current: currentPassword,
                      password: newPassword,
                    });
                    setShowPassword(false);
                    setCurrentPassword("");
                    setNewPassword("");
                    setNotice(
                      "Password changed. Other sessions have been signed out.",
                    );
                  });
                }}
              >
                <label>
                  Current password
                  <input
                    type="password"
                    autoComplete="current-password"
                    required
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                  />
                </label>
                <label>
                  New password
                  <input
                    type="password"
                    autoComplete="new-password"
                    required
                    minLength={10}
                    maxLength={128}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                  />
                </label>
                <button className="primary" disabled={busy}>
                  Update password
                </button>
              </form>
            </section>
          )}
          {!base ? (
            <div className="panel empty">Loading RFR27 base car…</div>
          ) : (
            <>
              {tab === "Vehicle editor" && (
                <>
                  <section className="base-strip">
                    <div className="file-icon">
                      <FolderOpen size={23} />
                    </div>
                    <div>
                      <span className="overline">BASE VEHICLE</span>
                      <strong>{display(base.name)}</strong>
                      <small>All changes are compared to this car.</small>
                    </div>
                    <span className="base-badge">
                      {Object.keys(csv).length
                        ? "CSV applied"
                        : "Original parameters"}
                    </span>
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => baseInput.current?.click()}
                    >
                      <Upload size={15} /> Load .OLVeh
                    </button>
                    <button
                      className="icon-button"
                      title="Restore RFR27 base car"
                      aria-label="Restore RFR27 base car"
                      onClick={() =>
                        void run(async () =>
                          replaceBase(
                            (await json<{ base: Values }>("/base")).base,
                          ),
                        )
                      }
                    >
                      <RotateCcw size={16} />
                    </button>
                  </section>
                  <div className="editor-layout">
                    <div className="editor-column">
                      <section className="panel editor">
                        <div className="panel-heading">
                          <div>
                            <h2>
                              Vehicle parameters{" "}
                              <span className="count">{changed} changed</span>
                            </h2>
                            <p>
                              Fine-tune your car. Changes update the curve live.
                            </p>
                          </div>
                          <button
                            className="small-button"
                            disabled={busy}
                            onClick={() => csvInput.current?.click()}
                          >
                            <FileSpreadsheet size={15} /> Import CSV
                          </button>
                        </div>
                        <nav
                          className="section-tabs"
                          aria-label="Parameter sections"
                        >
                          {groups.map((g) => (
                            <button
                              key={g}
                              className={g === active ? "active" : ""}
                              aria-pressed={g === active}
                              onClick={() => setActive(g)}
                            >
                              {g === "Quick adjustments" ? (
                                <Zap size={13} />
                              ) : null}
                              {g}
                            </button>
                          ))}
                        </nav>
                        <div className="fields-grid">
                          {fields
                            .filter((f) => f.group === active)
                            .map((f) => (
                              <FieldEditor
                                key={f.key}
                                field={f}
                                value={
                                  f.key in manual
                                    ? manual[f.key]
                                    : f.key in csv
                                      ? csv[f.key]
                                      : base[f.key]
                                }
                                fallback={
                                  f.key === "max_power_kw"
                                    ? Number(
                                        evaluation?.car.max_power_kw ??
                                          evaluation?.peak_power,
                                      )
                                    : f.key === "max_torque_nm"
                                      ? Number(
                                          evaluation?.car.max_torque_nm ??
                                            evaluation?.peak_torque,
                                        )
                                      : undefined
                                }
                                baseValue={base[f.key]}
                                source={
                                  f.key in manual
                                    ? "Manual"
                                    : f.key in csv
                                      ? "CSV"
                                      : "Base"
                                }
                                changed={
                                  f.key in overrides &&
                                  !same(evaluation?.car[f.key], base[f.key])
                                }
                                onChange={(v) => setField(f.key, v)}
                                onReset={() => reset(f.key)}
                              />
                            ))}
                        </div>
                        <div className="editor-footer">
                          <span>
                            <span className="changed-dot" /> Changed from base
                          </span>
                          <button
                            className="text-button"
                            onClick={() => {
                              setManual({});
                              setCsv({});
                              setNotice(
                                "All changes reset to the base vehicle.",
                              );
                            }}
                          >
                            Reset all changes <RotateCcw size={13} />
                          </button>
                        </div>
                      </section>
                      <section className="panel workflow">
                        <div className="workflow-icon">
                          <FileSpreadsheet size={23} />
                        </div>
                        <div>
                          <h3>Bring your parameters with you</h3>
                          <p>
                            Import a CSV, review the differences, then apply.
                          </p>
                          <small>
                            <code>parameter,value,unit</code> · e.g.{" "}
                            <code>mass,200,kg</code>
                          </small>
                        </div>
                        <button
                          className="icon-button"
                          aria-label="Import parameter CSV"
                          onClick={() => csvInput.current?.click()}
                        >
                          <ArrowRight size={18} />
                        </button>
                      </section>
                      <section className="panel save-row">
                        <div>
                          <h3>Keep this iteration</h3>
                          <p>
                            Save the base car and every override as a preset.
                          </p>
                        </div>
                        <button
                          className="secondary"
                          disabled={!valid}
                          onClick={() => {
                            setPresetName(display(evaluation?.car.name));
                            setTab("Saved presets");
                          }}
                        >
                          <Save size={15} /> Save preset
                        </button>
                      </section>
                    </div>
                    <aside className="analysis-column">
                      <section
                        className={`panel plot-panel ${!valid ? "stale" : ""}`}
                      >
                        <div className="panel-heading">
                          <div>
                            <span className="overline">POWERTRAIN</span>
                            <h2>Power & torque</h2>
                          </div>
                          <span className="live-badge">
                            <span className="status-dot" />
                            {valid ? "Live preview" : "Updating"}
                          </span>
                        </div>
                        <div className="peak-values">
                          <div>
                            <small>PEAK POWER</small>
                            <strong>
                              {evaluation?.peak_power.toFixed(1) ?? "—"}
                              <span>kW</span>
                            </strong>
                          </div>
                          <div>
                            <small>PEAK TORQUE</small>
                            <strong>
                              {evaluation?.peak_torque.toFixed(1) ?? "—"}
                              <span>Nm</span>
                            </strong>
                          </div>
                        </div>
                        <Curve evaluation={evaluation} />
                        <div className="chart-legend">
                          <span>
                            <i /> Power (kW)
                          </span>
                          <span>
                            <i /> Torque (Nm)
                          </span>
                        </div>
                        <div className="curve-note">
                          <CircleHelp size={14} />
                          <span>
                            Original curve is retained until power, torque, or
                            max RPM is edited.
                          </span>
                        </div>
                      </section>
                      <section className="panel metrics">
                        <div className="panel-heading">
                          <h2>At a glance</h2>
                          <Gauge size={17} />
                        </div>
                        <Metric
                          label="Kinematic top speed"
                          value={evaluation?.top_speed_kmh.toFixed(1)}
                          unit="km/h"
                        />
                        <Metric
                          label="Aero efficiency · Cl / Cd"
                          value={evaluation?.aero_efficiency.toFixed(3)}
                        />
                        <Metric
                          label="Overall gear ratios"
                          value={evaluation?.overall
                            .map((v) => v.toFixed(2))
                            .join(" / ")}
                        />
                        <small>
                          Top speed uses tire radius, rev limit, and gearing. It
                          does not account for drag or available power.
                        </small>
                      </section>
                      <section className="validation-card">
                        <ShieldCheck size={21} />
                        <div>
                          <strong>
                            {valid
                              ? "Ready to export"
                              : "Review your parameters"}
                          </strong>
                          <p>
                            {valid
                              ? "Inputs validated for .OLVeh export."
                              : "Export is available when all inputs are valid."}
                          </p>
                        </div>
                      </section>
                      {evaluation?.warnings.map((w) => (
                        <div className="message warning" key={w}>
                          <CircleHelp size={16} />
                          <span>{w}</span>
                        </div>
                      ))}
                    </aside>
                  </div>
                </>
              )}
              {tab === "Batch export" && (
                <section className="panel batch-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>One row. One car.</h2>
                      <p>
                        Each row overrides the current configuration. Blank
                        cells retain its values.
                      </p>
                    </div>
                    <Layers size={22} />
                  </div>
                  <div className="upload-zone">
                    <FileSpreadsheet size={36} />
                    <h3>{batchName || "Load your batch CSV"}</h3>
                    <p>
                      Use parameter names as headers. Values can include units.
                    </p>
                    <button
                      className="secondary"
                      onClick={() => batchInput.current?.click()}
                    >
                      <Upload size={15} /> Choose CSV
                    </button>
                  </div>
                  <label className="csv-label">
                    CSV preview / editor
                    <textarea
                      rows={9}
                      value={batchText}
                      onChange={(e) => setBatchText(e.target.value)}
                      placeholder={
                        "name,mass,max_power_kw\nRFR_Light,250 kg,75 kW\nRFR_Power,280 kg,90 kW"
                      }
                    />
                  </label>
                  <div className="batch-actions">
                    <span>
                      Up to 200 cars · Includes a base-difference manifest
                    </span>
                    <button
                      className="primary"
                      disabled={!valid || !batchText || busy}
                      onClick={() =>
                        void run(async () => {
                          await download("/batch", {
                            ...build,
                            text: batchText,
                          });
                          setNotice("Batch exported with manifest.csv.");
                        })
                      }
                    >
                      <ArrowDownToLine size={16} /> Export ZIP
                    </button>
                  </div>
                </section>
              )}
              {tab === "Saved presets" && (
                <>
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <h2>Save current setup</h2>
                        <p>The base and both override layers are included.</p>
                      </div>
                      <Save size={20} />
                    </div>
                    <div className="inline-form">
                      <label>
                        Preset name
                        <input
                          value={presetName}
                          maxLength={100}
                          placeholder="e.g. Endurance · low drag"
                          onChange={(e) => setPresetName(e.target.value)}
                        />
                      </label>
                      <button
                        className="primary"
                        disabled={!valid || !presetName.trim() || busy}
                        onClick={() =>
                          void run(async () => {
                            await json("/presets", {
                              name: presetName.trim(),
                              data: preset(),
                            });
                            await refreshPresets();
                            setPresetName("");
                            setNotice("Preset saved.");
                          })
                        }
                      >
                        <Plus size={16} /> Save preset
                      </button>
                      <button
                        className="secondary"
                        disabled={!valid}
                        onClick={() =>
                          saveBlob(
                            new Blob([JSON.stringify(preset(), null, 2)], {
                              type: "application/json",
                            }),
                            "car-preset.json",
                          )
                        }
                      >
                        Download JSON
                      </button>
                      <button
                        className="secondary"
                        onClick={() => presetInput.current?.click()}
                      >
                        <Upload size={15} /> Import JSON
                      </button>
                    </div>
                  </section>
                  <section className="panel">
                    <div className="panel-heading">
                      <h2>
                        Your presets{" "}
                        <span className="count">{presets.length}</span>
                      </h2>
                    </div>
                    {!presets.length ? (
                      <div className="empty">
                        <FolderOpen size={32} />
                        <h3>A place for your best setups</h3>
                        <p>
                          Save a configuration above to find it here next time.
                        </p>
                      </div>
                    ) : (
                      presets.map((p) => (
                        <div className="preset-item" key={p.id}>
                          <span className="file-icon">
                            <Layers size={20} />
                          </span>
                          <strong>{p.name}</strong>
                          <button
                            className="secondary"
                            disabled={busy}
                            onClick={() =>
                              void run(async () =>
                                applyPreset(await json(`/presets/${p.id}`)),
                              )
                            }
                          >
                            <FolderOpen size={14} /> Load
                          </button>
                          <button
                            className="icon-button"
                            aria-label={`Delete ${p.name}`}
                            disabled={busy}
                            onClick={() => {
                              if (window.confirm(`Delete preset “${p.name}”?`))
                                void run(async () => {
                                  await api(
                                    `/presets/${p.id}`,
                                    undefined,
                                    "DELETE",
                                  );
                                  await refreshPresets();
                                });
                            }}
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                      ))
                    )}
                  </section>
                </>
              )}
            </>
          )}
          <footer>
            <span>
              RFR27 <span> / </span> VEHICLE PERFORMANCE
            </span>
            <span>Built for the next iteration.</span>
          </footer>
        </main>
      </div>
      {fileInput(baseInput, "base", ".OLVeh")}
      {fileInput(csvInput, "csv", ".csv")}
      {fileInput(presetInput, "preset", ".json")}
      {fileInput(batchInput, "batch", ".csv")}
      {preview && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="csv-title"
          >
            <div className="panel-heading">
              <div>
                <div className="eyebrow">REVIEW BEFORE APPLYING</div>
                <h2 id="csv-title">Parameter changes</h2>
              </div>
              <button
                className="icon-button"
                aria-label="Close CSV preview"
                onClick={() => setPreview(null)}
              >
                <X size={20} />
              </button>
            </div>
            <p className="modal-description">
              CSV values update the CSV layer. Manual edits keep their priority.
            </p>
            {preview.errors.map((e, i) => (
              <div className="message error" key={i}>
                {e}
              </div>
            ))}
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Parameter</th>
                    <th>Current CSV / base</th>
                    <th>After import</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.changes.map((c) => (
                    <tr key={c.parameter}>
                      <td>{c.parameter}</td>
                      <td>
                        {display(c.before)} {c.unit}
                      </td>
                      <td className="after">
                        {display(c.after)} {c.unit}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!preview.changes.length && !preview.errors.length && (
              <p>No values differ from the current CSV layer.</p>
            )}
            <div className="modal-actions">
              <button className="secondary" onClick={() => setPreview(null)}>
                <ArrowLeft size={15} /> Cancel
              </button>
              <button
                className="primary"
                disabled={!!preview.errors.length}
                onClick={() => {
                  setCsv((old) => ({ ...old, ...preview.overrides }));
                  setPreview(null);
                  setNotice(
                    "CSV changes applied. Manual edits retain priority.",
                  );
                }}
              >
                <Check size={16} /> Apply changes
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
function FieldEditor({
  field: f,
  value,
  fallback,
  baseValue,
  source,
  changed,
  onChange,
  onReset,
}: {
  field: Field;
  value: unknown;
  fallback?: number;
  baseValue: unknown;
  source: string;
  changed: boolean;
  onChange: (v: unknown) => void;
  onReset: () => void;
}) {
  return (
    <div className={`field ${changed ? "changed" : ""}`}>
      <div className="field-label">
        <label htmlFor={`field-${f.key}`}>{f.label}</label>
        <span className={`source ${source.toLowerCase()}`}>{source}</span>
      </div>
      <div className="input-wrap">
        <input
          id={`field-${f.key}`}
          value={
            value === null && fallback !== undefined
              ? String(Number(fallback.toPrecision(9)))
              : display(value)
          }
          placeholder={f.key === "shift_rpm" ? "Follow max RPM" : ""}
          onChange={(e) => onChange(e.target.value)}
          inputMode={f.kind === "number" ? "decimal" : undefined}
          aria-describedby={`hint-${f.key}`}
        />
        {f.unit && <span className="unit">{f.unit}</span>}
        {source !== "Base" && (
          <button
            title={
              source === "Manual" ? "Reset to CSV or base" : "Reset to base"
            }
            aria-label={`Reset ${f.label}`}
            onClick={onReset}
          >
            <RotateCcw size={14} />
          </button>
        )}
      </div>
      <small id={`hint-${f.key}`}>
        {changed
          ? `Base: ${baseValue === null && fallback !== undefined ? "original curve" : display(baseValue) || "automatic"}${f.unit ? " " + f.unit : ""}`
          : f.kind === "array"
            ? "Separate entries with commas"
            : f.key === "cl"
              ? "Positive values represent downforce"
              : f.key === "max_power_kw" || f.key === "max_torque_nm"
                ? "Inferred from curve until edited"
                : f.units.length > 1
                  ? "Accepts units, e.g. " +
                    (f.key === "mass" ? "440 lb" : `${f.units[1]}`)
                  : " "}
      </small>
    </div>
  );
}
function Metric({
  label,
  value,
  unit,
}: {
  label: string;
  value?: string;
  unit?: string;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>
        {value ?? "—"} <small>{unit}</small>
      </strong>
    </div>
  );
}
function Curve({ evaluation: e }: { evaluation: Evaluation | null }) {
  const data =
    e?.rpm.map((rpm, i) => ({ rpm, power: e.power[i], torque: e.torque[i] })) ||
    [];
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={data}
          margin={{ top: 12, right: 0, left: -25, bottom: 15 }}
        >
          <defs>
            <linearGradient id="powerFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#e6283e" stopOpacity={0.14} />
              <stop offset="100%" stopColor="#e6283e" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#eceef1" vertical={false} />
          <XAxis
            dataKey="rpm"
            type="number"
            domain={[0, "dataMax"]}
            tick={{ fontSize: 10, fill: "#8a8f9a" }}
            tickLine={false}
            axisLine={false}
            label={{
              value: "ENGINE SPEED (RPM)",
              position: "bottom",
              fontSize: 9,
              fill: "#8a8f9a",
              offset: 0,
            }}
          />
          <YAxis
            yAxisId="power"
            tick={{ fontSize: 10, fill: "#8a8f9a" }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            yAxisId="torque"
            orientation="right"
            tick={{ fontSize: 10, fill: "#8a8f9a" }}
            tickLine={false}
            axisLine={false}
            width={32}
          />
          <Tooltip
            formatter={(v) =>
              typeof v === "number" ? v.toFixed(2) : String(v ?? "")
            }
            labelFormatter={(v) => `${v} rpm`}
            contentStyle={{
              borderRadius: 8,
              border: "1px solid #e5e7eb",
              fontSize: 12,
            }}
          />
          <Area
            yAxisId="power"
            name="Power (kW)"
            dataKey="power"
            stroke="#e6283e"
            fill="url(#powerFill)"
            strokeWidth={2.5}
            isAnimationActive={false}
          />
          <Line
            yAxisId="torque"
            name="Torque (Nm)"
            dataKey="torque"
            stroke="#343e52"
            strokeWidth={2}
            dot={false}
            strokeDasharray="5 4"
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
