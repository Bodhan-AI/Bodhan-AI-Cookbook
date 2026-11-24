
from __future__ import annotations

from collections.abc import Collection
from typing import Any

from bodhan_mcp.languages import normalize_lang_code, resolve_voice_id
from bodhan_mcp.models import ErrorCode, ToolError
from bodhan_mcp.tools.inspect import inspect_media
from bodhan_mcp.workspace import Workspace

from .tool_catalog import AGENT_TOOLS


def _issue(
    code: str,
    message: str,
    hint: str,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "code": code,
        "message": message,
        "hint": hint,
    }
    if source is not None:
        item["source"] = source
    return item


def _looks_like_file(value: str, suffixes: Collection[str]) -> bool:
    return Workspace.is_probable_file_ref(value, suffixes)


def _matches_json_type(value: Any, type_name: str) -> bool:
    if type_name == "null":
        return value is None
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    return False


def _schema_type(schema: dict[str, Any], *, field_name: str = "") -> str | None:
    type_name = schema.get("type")
    if isinstance(type_name, list):
        type_name = next((item for item in type_name if item != "null"), None)
    if type_name == "string" and field_name == "text":
        return "text"
    return type_name if isinstance(type_name, str) else None


def inspect_task_inputs(
    file_paths: list[str],
    workspace: Workspace,
) -> dict[str, Any]:
    """Inspect file safety, size, and formats before task planning or model APIs."""
    issues: list[dict[str, Any]] = []
    inspected_inputs: list[dict[str, Any]] = []
    supported_suffixes = set().union(*(spec.input_suffixes for spec in AGENT_TOOLS.values()))

    for file_path in file_paths:
        inspected = inspect_media(workspace, file_path)
        if not inspected.get("ok"):
            error = inspected.get("error", {})
            issues.append(
                _issue(
                    str(error.get("code", ErrorCode.INTERNAL.value)),
                    str(error.get("message", f"Could not inspect input {file_path!r}.")),
                    str(error.get("hint") or "Check that the file exists and is readable."),
                    source=file_path,
                )
            )
            continue

        data = inspected["data"]
        record = {
            "file_path": file_path,
            "file_type": data["file_type"],
            "extension": data["extension"],
            "size_bytes": data["size_bytes"],
            "mime_type": data["mime_type"],
            "duration_seconds": data.get("duration_seconds"),
            "page_count": data.get("page_count"),
            "metadata_method": data.get("metadata_method"),
        }
        inspected_inputs.append(record)
        extension = str(data["extension"]).lower()

        if extension not in supported_suffixes:
            issues.append(
                _issue(
                    ErrorCode.UNSUPPORTED_FORMAT.value,
                    f"Input format {extension or '(no extension)'!r} is not supported.",
                    "Supported extensions: " + ", ".join(sorted(supported_suffixes)) + ".",
                    source=file_path,
                )
            )

    if not file_paths:
        issues.append(
            _issue(
                ErrorCode.INVALID_ARGUMENT.value,
                "No readable input files were supplied.",
                "Pass at least one supported file with --files.",
            )
        )

    return {
        "ok": not issues,
        "inspected_inputs": inspected_inputs,
        "issues": issues,
    }


def validate_plan(
    plan: Any,
    workspace: Workspace,
    tool_catalog: dict[str, Any] | None = None,
    refusal: Any = None,
    deliverables: Any = None,
    input_manifest: list[dict[str, Any]] | None = None,
    needs_followup: bool = False,
) -> dict[str, Any]:
    """Validate planner-selected operations and their file/language arguments pre-execution."""
    issues: list[dict[str, Any]] = []
    if refusal is not None:
        if (
            not isinstance(refusal, dict)
            or not isinstance(refusal.get("code"), str)
            or refusal.get("code") not in {"CAPABILITY_UNAVAILABLE", "UNSUPPORTED_LANGUAGE"}
            or not isinstance(refusal.get("message"), str)
            or not refusal["message"].strip()
            or (
                "hint" in refusal
                and (not isinstance(refusal["hint"], str) or not refusal["hint"].strip())
            )
            or (
                "capability" in refusal
                and (
                    not isinstance(refusal["capability"], str) or not refusal["capability"].strip()
                )
            )
        ):
            return {
                "ok": False,
                "issues": [
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        "The planner returned a malformed task refusal.",
                        "Return a refusal object with a code and a clear message.",
                    )
                ],
            }
        if plan:
            return {
                "ok": False,
                "issues": [
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        "The planner returned both executable steps and a refusal.",
                        "Return either a supported plan or a refusal, not both.",
                    )
                ],
            }
        return {
            "ok": False,
            "issues": [
                _issue(
                    refusal["code"],
                    refusal["message"],
                    str(
                        refusal.get("hint")
                        or "Revise the request or configure the missing capability."
                    ),
                )
            ],
        }
    if not isinstance(plan, list):
        return {
            "ok": False,
            "issues": [
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    "The planner response must contain a list of steps.",
                    "Retry planning and require a version-1 plan with a JSON list of steps.",
                )
            ],
        }
    if not plan:
        issues.append(
            _issue(
                ErrorCode.INVALID_ARGUMENT.value,
                "The planner returned neither executable steps nor an explicit refusal.",
                "Retry planning; the response must include a complete plan or name the "
                "unavailable capability or language.",
            )
        )

    composable_plan = any(isinstance(step, dict) and "arguments" in step for step in plan)
    if composable_plan and any(isinstance(step, dict) and "arguments" not in step for step in plan):
        issues.append(
            _issue(
                ErrorCode.INVALID_ARGUMENT.value,
                "A plan cannot mix composable and legacy step formats.",
                "Use the version-1 id/tool/arguments format for every step.",
            )
        )
    if composable_plan and not deliverables and not needs_followup:
        issues.append(
            _issue(
                ErrorCode.INVALID_ARGUMENT.value,
                "A composable plan must declare at least one final deliverable.",
                "Select the requested final outputs in the plan's deliverables list.",
            )
        )

    available_outputs: dict[str, str] = {}
    step_outputs: dict[str, dict[str, str]] = {}
    step_array_item_fields: dict[str, dict[str, str]] = {}
    step_ids: set[str] = set()
    completed_step_ids: set[str] = set()
    catalog_tools = (
        {item.get("name"): item for item in tool_catalog.get("tools", [])}
        if tool_catalog is not None
        else {}
    )
    input_manifest = input_manifest or []
    for index, raw_step in enumerate(plan):
        source = f"plan step {index + 1}"
        if not isinstance(raw_step, dict):
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"{source} must be an object.",
                    "Provide each plan step as an object with a supported `tool`.",
                    source=source,
                )
            )
            continue

        standardized = "arguments" in raw_step
        step_id = raw_step.get("id", f"legacy_{index + 1}")
        source = f"plan step {step_id!r}"
        if standardized and "id" not in raw_step:
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"Composable {source} is missing its `id`.",
                    "Assign every composable step a unique identifier.",
                    source=source,
                )
            )
        if not isinstance(step_id, str) or not step_id.strip():
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"`id` in {source} must be a non-empty string.",
                    "Give every step a unique, non-empty identifier.",
                    source=source,
                )
            )
            step_id = f"invalid_{index + 1}"
        if step_id in step_ids:
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"Duplicate step identifier {step_id!r}.",
                    "Assign each plan step a unique identifier.",
                    source=source,
                )
            )
        step_ids.add(step_id)

        control_fields = {"id", "tool", "arguments", "for_each", "when"}
        if standardized and set(raw_step) - control_fields:
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"Unexpected fields in {source}: "
                    + ", ".join(sorted(set(raw_step) - control_fields)),
                    "Use only id, tool, arguments, for_each, and when.",
                    source=source,
                )
            )
        if standardized:
            step = raw_step.get("arguments")
            if not isinstance(step, dict):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"`arguments` in {source} must be an object.",
                        "Provide a JSON object matching the selected tool's input schema.",
                        source=source,
                    )
                )
                step = {}
        else:
            step = {key: value for key, value in raw_step.items() if key != "tool"}
        tool = raw_step.get("tool")
        if not isinstance(tool, str) or tool not in AGENT_TOOLS:
            issues.append(
                _issue(
                    "TOOL_UNSUPPORTED",
                    f"Planned operation {tool!r} is not supported.",
                    f"Choose one of the catalogued operations: {', '.join(AGENT_TOOLS)}.",
                    source=source,
                )
            )
            continue

        spec = AGENT_TOOLS[tool]
        current_step_outputs: dict[str, str] = {}
        step_outputs[step_id] = current_step_outputs
        catalog_entry = catalog_tools.get(tool)
        if tool_catalog is not None and (
            catalog_entry is None or not catalog_entry.get("available", False)
        ):
            reason = (
                catalog_entry.get("availability_reason")
                if catalog_entry is not None
                else "The operation is missing from the current capability catalog."
            )
            issues.append(
                _issue(
                    "TOOL_UNAVAILABLE",
                    f"Planned operation {tool!r} is not available in the current configuration.",
                    reason or "Configure the required model credentials and retry.",
                    source=source,
                )
            )

        allowed_fields = set(spec.input_schema["properties"])
        for field in sorted(step.keys() - allowed_fields, key=str):
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"Unexpected `{field}` in {source}.",
                    f"Remove the field; supported fields are {', '.join(sorted(allowed_fields))}.",
                    source=source,
                )
            )

        def resolve_ref(
            value: Any,
            *,
            expected: str | None = None,
            source_name: str = source,
        ) -> str | None:
            if not isinstance(value, dict) or set(value) != {"$ref"}:
                return None
            ref = value["$ref"]
            if not isinstance(ref, dict) or set(ref) != {"step_id", "output"}:
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"Malformed output reference in {source_name}.",
                        'Use {"$ref":{"step_id":"prior_step","output":"output_key"}}.',
                        source=source_name,
                    )
                )
                return "invalid"
            target_id, output = ref.get("step_id"), ref.get("output")
            if not isinstance(target_id, str) or not isinstance(output, str):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"Output reference in {source_name} needs string "
                        "step_id and output fields.",
                        "Reference a named output from an earlier step.",
                        source=source_name,
                    )
                )
                return "invalid"
            output_type = (
                step_outputs.get(target_id, {}).get(output)
                if target_id in completed_step_ids
                else None
            )
            if output_type is None:
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"Output reference {target_id}.{output} in {source_name} is unknown or not "
                        "produced by an earlier step.",
                        "Reference an output declared by a preceding step.",
                        source=source_name,
                    )
                )
                return "invalid"
            if expected is not None and not (
                expected == "string" and output_type == "text" or expected == output_type
            ):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"Output reference {target_id}.{output} has type {output_type!r}; "
                        f"`{expected}` is required in {source_name}.",
                        "Bind an earlier output whose type matches the input field.",
                        source=source_name,
                    )
                )
            return output_type

        for_each = raw_step.get("for_each")
        item_fields: dict[str, str] = {}
        if for_each is not None:
            if not standardized or not isinstance(for_each, dict) or set(for_each) != {
                "source",
                "as",
            }:
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"`for_each` in {source} must contain exactly source and as.",
                        'Use {"source":{"input":"files"},"as":"item"} or a prior array output.',
                        source=source,
                    )
                )
            else:
                alias = for_each.get("as")
                source_spec = for_each.get("source")
                if alias != "item":
                    issues.append(
                        _issue(
                            ErrorCode.INVALID_ARGUMENT.value,
                            f"`for_each.as` in {source} must be `item`.",
                            "Use the `$item` binding to select fields from each item.",
                            source=source,
                        )
                    )
                if source_spec == {"input": "files"}:
                    item_fields = {
                        key: item_type
                        for item in input_manifest
                        for key, value in item.items()
                        if (item_type := _schema_type({"type": _python_json_type(value)}))
                    }
                elif isinstance(source_spec, dict) and set(source_spec) == {
                    "step_id",
                    "output",
                }:
                    target_id = source_spec.get("step_id")
                    output = source_spec.get("output")
                    output_type = (
                        step_outputs.get(target_id, {}).get(output)
                        if (
                            isinstance(target_id, str)
                            and isinstance(output, str)
                            and target_id in completed_step_ids
                        )
                        else None
                    )
                    if output_type != "array":
                        issues.append(
                            _issue(
                                ErrorCode.INVALID_ARGUMENT.value,
                                f"`for_each` in {source} must reference an earlier array output.",
                                "Select an array output produced by a preceding step.",
                                source=source,
                            )
                        )
                    else:
                        item_fields = step_array_item_fields.get(f"{target_id}.{output}", {})
                else:
                    issues.append(
                        _issue(
                            ErrorCode.INVALID_ARGUMENT.value,
                            f"Invalid `for_each.source` in {source}.",
                            'Use {"input":"files"} or an earlier step_id/output array reference.',
                            source=source,
                        )
                    )
        for field, value in step.items():
            field_schema = spec.input_schema["properties"].get(field)
            if field_schema is None:
                continue
            expected_types = field_schema.get("type")
            if expected_types is None:
                continue
            if isinstance(expected_types, str):
                expected_types = [expected_types]
            if isinstance(value, dict) and set(value) == {"$ref"}:
                expected = "string" if "string" in expected_types else expected_types[0]
                resolve_ref(value, expected=expected)
                continue
            if isinstance(value, dict) and set(value) == {"$item"}:
                item_field = value["$item"]
                if (
                    not isinstance(item_field, str)
                    or item_field not in item_fields
                    or not any(
                        _matches_json_type("", expected_type)
                        if item_fields[item_field] == "text"
                        else item_fields[item_field] == expected_type
                        for expected_type in expected_types
                    )
                ):
                    issues.append(
                        _issue(
                            ErrorCode.INVALID_ARGUMENT.value,
                            f"Invalid item binding for `{field}` in {source}.",
                            "Bind a field supplied by the selected for_each source with a "
                            "compatible type.",
                            source=source,
                        )
                    )
                continue
            if not any(_matches_json_type(value, type_name) for type_name in expected_types):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"`{field}` in {source} must be {' or '.join(expected_types)}.",
                        "Use a value matching the tool's input schema.",
                        source=source,
                    )
                )
                continue
            if "enum" in field_schema and value not in field_schema["enum"]:
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"`{field}` in {source} must be one of {field_schema['enum']!r}.",
                        "Use a value matching the tool's input schema.",
                        source=source,
                    )
                )
                continue
            if isinstance(value, int | float) and not isinstance(value, bool):
                minimum = field_schema.get("minimum")
                if minimum is not None and value < minimum:
                    issues.append(
                        _issue(
                            ErrorCode.INVALID_ARGUMENT.value,
                            f"`{field}` in {source} must be at least {minimum}.",
                            "Use a value matching the tool's input schema.",
                            source=source,
                        )
                    )

        required_fields = spec.input_schema["required"]
        for field in required_fields:
            if field not in step or (isinstance(step.get(field), str) and not step[field].strip()):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"Missing required `{field}` in {source}.",
                        "Provide the required argument using the tool's documented input schema.",
                        source=source,
                    )
                )

        for field in sorted(spec.language_fields):
            value = step.get(field)
            if value is None:
                continue
            if isinstance(value, dict) and set(value) == {"$ref"}:
                resolve_ref(value, expected="string")
                continue
            if isinstance(value, dict) and set(value) == {"$item"}:
                item_field = value["$item"]
                if not isinstance(item_field, str) or item_fields.get(item_field) not in {
                    "string",
                    "text",
                }:
                    issues.append(
                        _issue(
                            ErrorCode.INVALID_ARGUMENT.value,
                            f"`{field}` in {source} must bind a string item field.",
                            "Choose a language string from the current for_each item.",
                            source=source,
                        )
                    )
                continue
            if not isinstance(value, str):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"`{field}` in {source} must be a string.",
                        "Choose a configured language code or name.",
                        source=source,
                    )
                )
                continue
            try:
                normalized = normalize_lang_code(value)
                if tool_catalog is not None:
                    supported_languages = set(tool_catalog.get("supported_languages", []))
                    if normalized not in supported_languages:
                        raise ToolError(
                            ErrorCode.UNSUPPORTED_LANGUAGE,
                            f"Language {value!r} is not available in the current "
                            "capability catalog.",
                            hint="Choose a language listed in the task-agent capability catalog.",
                        )
            except ToolError as exc:
                issues.append(
                    _issue(
                        exc.code.value,
                        f"Unsupported `{field}` in {source}: {value!r}.",
                        exc.hint or "Choose a configured language.",
                        source=source,
                    )
                )

        if tool in {"translate", "speak"}:
            text = step.get("text")
            source_key = step.get("source_key", "text")
            if source_key is not None and (
                not isinstance(source_key, str) or not source_key.strip()
            ):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"`source_key` in {source} must be a non-empty string.",
                        "Provide text directly or reference an earlier step's output.",
                        source=source,
                    )
                )
            elif (not isinstance(text, str) or not text) and not (
                isinstance(text, dict) and set(text) in ({"$ref"}, {"$item"})
            ):
                key = source_key or "text"
                output_type = available_outputs.get(key)
                if output_type != "text":
                    detail = (
                        f"has type {output_type!r}, not text"
                        if output_type is not None
                        else "is unavailable"
                    )
                    issues.append(
                        _issue(
                            ErrorCode.INVALID_ARGUMENT.value,
                            f"{tool.title()} in {source} references output {key!r}, "
                            f"which {detail}.",
                            "Provide non-empty `text` or reference an output from an earlier step.",
                            source=source,
                        )
                    )

        if tool == "speak":
            lang_val = step.get("language")
            voice_val = step.get("voice_id")
            gender_val = step.get("voice_gender")
            if voice_val is not None and (
                not isinstance(voice_val, str) or not voice_val.strip()
            ):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"`voice_id` in {source} must be a non-empty string.",
                        "Provide a verified Bodhan voice ID or omit the field.",
                        source=source,
                    )
                )
            elif (
                isinstance(lang_val, str)
                and isinstance(voice_val, str)
                and voice_val.strip()
            ):
                try:
                    resolve_voice_id(lang_val, voice_id=voice_val)
                except ToolError as exc:
                    issues.append(
                        _issue(
                            exc.code.value,
                            exc.message,
                            exc.hint or "Use a verified Bodhan voice name.",
                            source=source,
                        )
                    )
            if gender_val is not None and (
                not isinstance(gender_val, str)
                or gender_val.strip().lower() not in {"male", "female"}
            ):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"`voice_gender` in {source} must be 'female' or 'male'.",
                        "Pass 'female' or 'male', or specify voice_id directly.",
                        source=source,
                    )
                )

        if tool in {"transcribe", "ocr"}:
            value = step.get("file_path")
            suffixes = spec.input_suffixes
        elif tool == "detect_language":
            value = step.get("text_or_file")
            suffixes = spec.input_suffixes
        else:
            value = step.get("text")
            suffixes = spec.input_suffixes

        if isinstance(value, str) and _looks_like_file(value, suffixes):
            try:
                workspace.resolve_input(value, suffixes=suffixes)
            except ToolError as exc:
                issues.append(
                    _issue(
                        exc.code.value,
                        f"Invalid planned input in {source}: {exc.message}",
                        exc.hint or "Use an existing input in a supported format.",
                        source=source,
                    )
                )

        if standardized:
            current_step_outputs.update(spec.output_types)
            for output, output_schema in spec.output_schema.get("properties", {}).items():
                if output_schema.get("type") == "array":
                    properties = output_schema.get("items", {}).get("properties", {})
                    step_array_item_fields[f"{step_id}.{output}"] = {
                        field_name: field_type
                        for field_name, field_schema in properties.items()
                        if (field_type := _schema_type(field_schema, field_name=field_name))
                    }
            if for_each is not None:
                original_output_types = dict(current_step_outputs)
                current_step_outputs.update({output: "array" for output in original_output_types})
                for output, output_type in original_output_types.items():
                    if output_type != "array":
                        step_array_item_fields[f"{step_id}.{output}"] = {"value": output_type}
            available_outputs.update(current_step_outputs)
        else:
            available_outputs.update(spec.output_types)

        when = raw_step.get("when")
        if when is not None:
            operators = {
                "truthy",
                "equals",
                "not_equals",
                "greater_than",
                "greater_or_equal",
                "less_than",
                "less_or_equal",
                "in",
                "contains",
            }
            if (
                not standardized
                or not isinstance(when, dict)
                or set(when) - {"ref", "operator", "value"}
                or not isinstance(when.get("ref"), dict)
                or set(when.get("ref", {})) != {"step_id", "output"}
                or not isinstance(when.get("operator"), str)
                or when.get("operator") not in operators
                or (when.get("operator") == "truthy" and "value" in when)
            ):
                issues.append(
                    _issue(
                        ErrorCode.INVALID_ARGUMENT.value,
                        f"Malformed `when` condition in {source}.",
                        "Use a prior-output ref, a supported operator, and a value when needed.",
                        source=source,
                    )
                )
            else:
                condition_ref = {
                    "$ref": {
                        "step_id": when["ref"].get("step_id"),
                        "output": when["ref"].get("output"),
                    }
                }
                condition_type = resolve_ref(condition_ref)
                if when["operator"] != "truthy" and "value" not in when:
                    issues.append(
                        _issue(
                            ErrorCode.INVALID_ARGUMENT.value,
                            f"`when.value` is required for {when['operator']!r} in {source}.",
                            "Provide the comparison value for this condition.",
                            source=source,
                        )
                    )
                elif when["operator"] == "truthy":
                    pass
                elif condition_type not in {None, "invalid"}:
                    value_type = _python_json_type(when.get("value"))
                    operator = when["operator"]
                    compatible = (
                        operator in {"equals", "not_equals"}
                        and (
                            value_type == condition_type
                            or value_type in {"integer", "number"}
                            and condition_type in {"integer", "number"}
                            or value_type == "string"
                            and condition_type == "text"
                        )
                        or operator
                        in {"greater_than", "greater_or_equal", "less_than", "less_or_equal"}
                        and condition_type in {"integer", "number"}
                        and value_type in {"integer", "number"}
                        or operator == "in"
                        and value_type in {"array", "string", "object"}
                        or operator == "contains"
                        and condition_type in {"array", "string", "text", "object"}
                    )
                    if not compatible:
                        issues.append(
                            _issue(
                                ErrorCode.INVALID_ARGUMENT.value,
                                f"Condition operator {operator!r} is incompatible with "
                                f"output type {condition_type!r} in {source}.",
                                "Use an operator compatible with the referenced output type.",
                                source=source,
                            )
                        )
        completed_step_ids.add(step_id)

    if deliverables is None:
        deliverables = []
    if not isinstance(deliverables, list):
        issues.append(
            _issue(
                ErrorCode.INVALID_ARGUMENT.value,
                "`deliverables` must be a list.",
                "Declare each requested final output as a named step output.",
            )
        )
        deliverables = []
    names: set[str] = set()
    media_types = {
        "text": {"text"},
        "subtitle": {"text"},
        "document": {"text"},
        "audio": {"audio", "audio_list"},
        "json": {"object", "array"},
        "image": {"image"},
    }
    for index, deliverable in enumerate(deliverables):
        source = f"deliverable {index + 1}"
        required = {"name", "step_id", "output", "media_type", "description"}
        if not isinstance(deliverable, dict) or set(deliverable) != required:
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"{source} must contain name, step_id, output, media_type, and description.",
                    "Select a typed output from a plan step and describe its final use.",
                    source=source,
                )
            )
            continue
        name, step_id, output = (
            deliverable.get("name"),
            deliverable.get("step_id"),
            deliverable.get("output"),
        )
        media_type = deliverable.get("media_type")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name in names
            or not isinstance(step_id, str)
            or not isinstance(output, str)
            or not isinstance(media_type, str)
            or not isinstance(deliverable.get("description"), str)
            or not deliverable["description"].strip()
        ):
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"{source} has invalid or duplicate fields.",
                    "Use unique names and non-empty strings for all descriptive fields.",
                    source=source,
                )
            )
            continue
        names.add(name)
        output_type = step_outputs.get(step_id, {}).get(output)
        if output_type is None:
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"{source} references unknown output {step_id}.{output}.",
                    "Select an output produced by a declared plan step.",
                    source=source,
                )
            )
        elif output_type not in media_types.get(media_type, set()):
            issues.append(
                _issue(
                    ErrorCode.INVALID_ARGUMENT.value,
                    f"{source} media_type {media_type!r} is incompatible with {output_type!r}.",
                    "Choose a media_type compatible with the selected output.",
                    source=source,
                )
            )

    return {"ok": not issues, "issues": issues}


def _python_json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "null"