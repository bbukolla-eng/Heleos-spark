# Invoke with /usr/bin/jq -e -f governance/agents/validate-contracts.jq RECORD.json.
# One JSON record per invocation. Only a boolean leaves this filter; no input is
# echoed, including on type errors. Admission, DNS/egress mediation, hash-byte
# verification, citation checking and independent review remain controller work.
def exact($names): type == "object" and (keys == ($names | sort));
def member($values): . as $value | ($values | index($value)) != null;
def providers: ["codex", "claude_code", "kimi", "grok", "cursor", "notebook_lm", "grok_bots"];
def bounded_string($max): type == "string" and length >= 1 and length <= $max and (test("[\\x00-\\x1f\\x7f]") | not);
def identifier: bounded_string(128) and test("^[a-z0-9][a-z0-9_-]*$");
def digest: bounded_string(64) and test("^[0-9a-f]{64}$");
# A sanitation grammar, not a classifier for arbitrary confidential content.
# Ratios, percentages and currency are ordinary prose; host paths, recognizable
# credential material and interpolation syntax are not accepted research text.
def unsafe_content:
  test("(^|[^a-z0-9])/([a-z0-9_.~]|/)|[a-z]:[/\\\\]|\\\\|file[ ]*:|~[/\\\\]|\\.\\.[/\\\\]|/\\.\\.($|[/ ])|(^|[/ =:])([.]ssh|[.]aws|[.]config|[.]codex|private|credentials|secrets|users|home)(/|$)|%[a-z_][a-z0-9_]*%|%(2f|5c|2e)|[$][({a-z_]|`|\\{\\{|\\}\\}|#\\{"; "i")
  or test("(token|api[_ -]?key|password|passwd|secret|credential|cookie|authorization)[\"']?[ ]*[:=]|bearer[ ]+[a-z0-9._~-]+|sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9]{16,}|github_pat_[a-z0-9_]{16,}|AKIA[A-Z0-9]{16}|-----BEGIN[ A-Z]*PRIVATE KEY-----|eyJ[a-z0-9_-]+[.]eyJ[a-z0-9_-]+[.][a-z0-9_-]+"; "i");
def prose: bounded_string(2048) and test("^[\\x20-\\x7e]+$") and (unsafe_content | not) and test("[^ ]");
def array_bound($min): type == "array" and length >= $min and length <= 64;
def texts: array_bound(0) and all(.[]; prose);
def endpoint:
  bounded_string(512)
  and test("^https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\\.[a-z]{2,63}$")
  and (test("\\.(localhost|local|internal|lan|home|test|invalid)$") | not);
def source_url:
  bounded_string(2048)
  and (split("/")[:3] | join("/") | endpoint)
  and test("^https://[^/]+(/[A-Za-z0-9._~-]+)*/?$")
  and (test("/\\.\\.?(/|$)") | not);
def integer_between($min; $max): type == "number" and . >= $min and . <= $max and floor == .;
def budgets:
  exact(["wall_seconds", "actions", "output_bytes", "cost_usd"])
  and (.wall_seconds | integer_between(1; 86400))
  and (.actions | integer_between(1; 10000))
  and (.output_bytes | integer_between(1; 10485760))
  and (.cost_usd | type == "number" and . > 0 and . <= 1000);
def utc_time:
  bounded_string(20)
  and test("^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$")
  and (. as $time | (fromdateiso8601 | todateiso8601) == $time);
def argument:
  bounded_string(256) and test("^[A-Za-z0-9_.=-][A-Za-z0-9_./=-]*$")
  and (test("(^|[/=])\\.\\.(/|$)") | not)
  and (unsafe_content | not);
def command:
  exact(["tool", "argv", "expected_exit"])
  and (.tool | identifier)
  and (.argv | array_bound(0) and all(.[]; argument))
  and (.expected_exit | integer_between(0; 255));
def worker:
  exact(["schema", "task_id", "provider", "executable_sha256", "snapshot_sha256", "data_class", "allowed_inputs", "allowed_tools", "allowed_endpoints", "forbidden_paths", "forbidden_actions", "acceptance_commands", "budgets", "result_directory", "return_format", "authority"])
  and .schema == "heleos.research-worker-contract/v1"
  and (.task_id | identifier)
  and (.provider | member(providers))
  and (.executable_sha256 | digest) and (.snapshot_sha256 | digest)
  and .data_class == "PUBLIC"
  and (.allowed_inputs | array_bound(1) and all(.[]; exact(["id", "sha256"]) and (.id | identifier) and (.sha256 | digest)))
  and (.allowed_tools | array_bound(1) and all(.[]; member(["read_public_source", "search_public_sources", "synthesize", "write_quarantine"])))
  and (.allowed_endpoints | array_bound(1) and all(.[]; endpoint))
  and (.forbidden_paths | array_bound(4) and sort == ["credentials", "host", "private", "production"])
  and (.forbidden_actions | array_bound(6) and sort == ["merge", "publish", "push", "read_credentials", "self_approve", "write_production"])
  and (.acceptance_commands | array_bound(1) and all(.[]; command))
  and (.budgets | budgets)
  and (.result_directory | bounded_string(36) and test("^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"))
  and .return_format == "heleos.research-packet/v1"
  and .authority == "proposal_only";
def packet:
  exact(["schema", "provider", "status", "purpose", "data_class", "source_sha256", "input_sha256", "query_or_case_id", "retrieved_at", "output_sha256", "citations", "claims", "contradictions", "gaps", "evaluation", "reviewer", "disposition", "budgets"])
  and .schema == "heleos.research-packet/v1"
  and (.provider | member(providers))
  and (.status | member(["completed", "unavailable", "unauthenticated", "sandbox_unavailable", "failed"]))
  and (.purpose | prose) and .data_class == "PUBLIC"
  and (.source_sha256 | array_bound(1) and all(.[]; digest))
  and (.input_sha256 | digest) and (.output_sha256 | digest)
  and (.query_or_case_id | prose) and (.retrieved_at | utc_time)
  and (.citations | array_bound(0) and all(.[]; exact(["url", "locator", "source_sha256"]) and (.url | source_url) and (.locator | prose) and (.source_sha256 | digest)))
  and (.source_sha256 as $sources | all(.citations[]; .source_sha256 | member($sources)))
  and (.claims | texts) and (.contradictions | texts) and (.gaps | texts)
  and (.evaluation | exact(["boundary_passes", "capability_passes", "reason"]) and (.boundary_passes | integer_between(0; 3)) and (.capability_passes | integer_between(0; 5)) and (.reason | prose))
  and (.evaluation.capability_passes == 0 or .evaluation.boundary_passes == 3)
  and (.reviewer | member(providers + ["human"])) and .reviewer != .provider
  and (.disposition | member(["pending", "rejected", "research_only", "disabled"]))
  and (.budgets | budgets)
  and (if .status == "completed" then
    ((.claims | length) == 0 or (.citations | length) > 0)
  else
    .claims == [] and .citations == [] and (.disposition | member(["pending", "rejected", "disabled"])) and .evaluation.boundary_passes == 0 and .evaluation.capability_passes == 0
  end);
if isempty(inputs) then
  try (if type != "object" then false
     elif .schema == "heleos.research-worker-contract/v1" then worker
     elif .schema == "heleos.research-packet/v1" then packet
     else false end) catch false
else false, (null | halt_error(1)) end
