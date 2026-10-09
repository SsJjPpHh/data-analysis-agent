# -*- coding: utf-8 -*-
"""OpenAI tool schemas for the research statistics workbench."""

TOOL_SCHEMA_VERSION = "research-2.0"

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspect_dataset",
            "description": (
                "Inspect an uploaded research dataset using the configured R backend. "
                "Use this to summarize columns, types, missingness, and basic structure "
                "before writing R analysis code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset_id": {
                        "type": "string",
                        "description": (
                            "Optional uploaded research dataset ID. Leave empty to use "
                            "the current session's first uploaded research dataset. "
                            "Do not infer this from the file name or display name; use "
                            "only an exact dataset_id returned by a previous tool result."
                        ),
                    },
                    "column_names": {
                        "type": "array", "items": {"type": "string"}, "maxItems": 20,
                        "description": "Optional exact variable names for detailed lookup from the saved local profile; up to 20 per call.",
                    },
                    "refresh": {
                        "type": "boolean", "description": "Re-run R inspection instead of reusing the current dataset profile. Defaults to false.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_analysis_plan",
            "description": (
                "Freeze the analysis intent, variable bindings, method family, diagnostics, "
                "and the request's baseline outputs for the active dataset before run_r_code. "
                "The backend expands those baselines into a data- and R-capability-aware scientific "
                "output portfolio. Use this once "
                "for a natural-language analysis request after inspecting the data as needed. "
                "Template requests already receive a backend-compiled plan."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "intent": {"type": "string", "minLength": 1, "maxLength": 500},
                    "category": {
                        "type": "string",
                        "enum": [
                            "基础统计",
                            "临床研究",
                            "公共卫生",
                            "临床试验与证据合成",
                            "行为与社会科学",
                            "机器学习",
                            "时间空间与文本",
                            "组学与生物信息",
                        ],
                    },
                    "method_family": {"type": "string", "minLength": 1, "maxLength": 120},
                    "scenario": {"type": "string", "minLength": 1, "maxLength": 500},
                    "variable_bindings": {
                        "type": "object",
                        "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 160},
                        "maxProperties": 30,
                    },
                    "analysis_inputs": {
                        "type": "array",
                        "description": (
                            "Typed scientific inputs used by the same AnalysisPlan model as templates. "
                            "Use column/columns for dataset fields and parameter/level/specification for "
                            "non-column analysis choices. A level input must declare depends_on as the "
                            "stable ID of its parent column input, and its value must be an observed level."
                        ),
                        "minItems": 1,
                        "maxItems": 30,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "pattern": "^[a-z][a-z0-9_]*$",
                                    "maxLength": 80,
                                },
                                "label": {"type": "string", "minLength": 1, "maxLength": 160},
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "dataset",
                                        "column",
                                        "columns",
                                        "level",
                                        "parameter",
                                        "object",
                                        "specification",
                                    ],
                                },
                                "value": {},
                                "provenance": {
                                    "type": "string",
                                    "enum": ["user", "inferred", "default"],
                                },
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                "depends_on": {
                                    "type": "string",
                                    "pattern": "^[a-z][a-z0-9_]*$",
                                    "maxLength": 80,
                                },
                            },
                            "required": ["id", "label", "kind", "value", "provenance"],
                        },
                    },
                    "package_queries": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 120},
                        "maxItems": 12,
                    },
                    "prerequisites": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 300},
                        "maxItems": 20,
                    },
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 300},
                        "maxItems": 20,
                    },
                    "diagnostics": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 300},
                        "minItems": 1,
                        "maxItems": 20,
                    },
                    "required_outputs": {
                        "type": "array",
                        "description": (
                            "Required outputs explicitly implied by the request. This is a baseline, not "
                            "an exhaustive ceiling; the backend adds applicable professional outputs."
                        ),
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "pattern": "^[a-z][a-z0-9_]*$",
                                    "maxLength": 80,
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": ["table", "figure", "text", "model"],
                                },
                                "required": {"type": "boolean"},
                                "label": {"type": "string", "maxLength": 160},
                            },
                            "required": ["role", "kind"],
                        },
                    },
                    "resource_class": {
                        "type": "string",
                        "enum": ["standard", "extended"],
                        "description": (
                            "Use standard for routine analyses. Use extended only for large "
                            "resampling/simulation, high-dimensional models, omics, imaging, "
                            "or large scientific objects that can benefit from a larger local "
                            "CPU and memory budget."
                        ),
                    },
                },
                "required": [
                    "intent",
                    "category",
                    "method_family",
                    "scenario",
                    "variable_bindings",
                    "analysis_inputs",
                    "package_queries",
                    "prerequisites",
                    "assumptions",
                    "diagnostics",
                    "required_outputs",
                    "resource_class",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_r_code",
            "description": (
                "Run R code against an uploaded research dataset. The dataset is available "
                "as the R object `dataset`. Use baa_table(), baa_table_one(), baa_plot(), "
                "baa_text(), and baa_model_summary() to persist outputs. Each call uses "
                "an isolated artifact directory. The frozen plan supplies adaptive local "
                "resources; baa_workers() and baa_parallel_lapply() are available for "
                "independent expensive tasks. Do not read previous saved artifact "
                "filenames as input paths; use artifact_previews or recompute from "
                "`dataset`. For baseline characteristics / Table One, prefer "
                "baa_table_one() so the right-side result panel receives one "
                "publication-style table with one categorical level per row; "
                "do not hand-build side-by-side left_join/full_join Table One "
                "tables or collapse levels into one cell. Do not use gt/flextable/"
                "gtsave/DOCX export code for Table One because the result panel handles "
                "formal exports from the saved artifact. Do not spend R calls inspecting "
                "baa_* helper existence/signatures or reading saved artifact files for "
                "preview; artifact_previews and the result panel handle that. Any numeric "
                "claim in the final "
                "answer must come from this tool result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "R code to execute.",
                    },
                    "dataset_id": {
                        "type": "string",
                        "description": (
                            "Optional uploaded research dataset ID. Leave empty to use "
                            "the current session's first uploaded research dataset. "
                            "Do not infer this from the file name or display name; use "
                            "only an exact dataset_id returned by a previous tool result."
                        ),
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "Optional execution deadline in seconds. Use 0 or omit it for the "
                            "local runtime policy (no fixed analysis deadline by default)."
                        ),
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_r_package_inventory",
            "description": (
                "Search or inspect the R package capabilities available to the configured "
                "user's active local R runtime. Use this before specialized, cross-disciplinary, "
                "or package-sensitive analyses so you can select appropriate installed R "
                "packages instead of relying on a small fixed method list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Optional analysis intent, domain, method, or package keyword, "
                            "for example 'survival', 'causal inference', 'single-cell', "
                            "'问卷', '转录组', or '空间分析'."
                        ),
                    },
                    "include_all": {
                        "type": "boolean",
                        "description": (
                            "Whether to include the full installed and declared package name lists. "
                            "Leave false for normal routing; set true when you need exhaustive "
                            "package discovery."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of package matches or names to return. Defaults to 80.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_r_package_help",
            "description": (
                "Inspect installed R package documentation signals from the current "
                "R runtime: package metadata, explicit exported functions, help.search "
                "topics, concrete help documents, and vignettes. Use this after "
                "selecting candidate packages when you need function names or syntax "
                "guidance without relying on hardcoded templates. If the package is "
                "unknown, omit package/packages and provide query to search across the "
                "installed R help index."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": (
                            "One R package name to inspect, for example 'survival' "
                            "or 'DESeq2'. Omit when you need cross-package help search."
                        ),
                    },
                    "packages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of up to 5 R package names to inspect together.",
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Optional function/method keyword for help.search and export filtering, "
                            "for example 'coxph', 'roc', 'enrichment', or 'umap'. "
                            "When package is omitted, this is used for cross-package search."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matched exports/help topics per package. Defaults to 30.",
                    },
                    "include_docs": {
                        "type": "boolean",
                        "description": (
                            "Whether to include bounded text from concrete R help documents. "
                            "Defaults to true and is useful before generating code with unfamiliar functions."
                        ),
                    },
                    "doc_limit": {
                        "type": "integer",
                        "description": "Maximum number of concrete help documents to include per package. Defaults to 3.",
                    },
                    "max_doc_chars": {
                        "type": "integer",
                        "description": "Maximum characters per help document. Defaults to 4000.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "Ask a concise clarifying question when the analysis goal or variable roles "
                "are ambiguous. Present 2-6 short clickable options. Do not include an "
                "'Other' option because the UI adds a custom-answer path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The clarifying question to show the user.",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2-6 predefined answer options.",
                        "minItems": 2,
                        "maxItems": 6,
                    },
                    "multi_select": {
                        "type": "boolean",
                        "description": "Whether the user may select multiple options.",
                    },
                },
                "required": ["question", "options"],
            },
        },
    },
]
