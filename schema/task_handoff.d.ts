/**
 * Task Handoff Protocol & Schema Declarations
 *
 * Defines TypeScript interfaces and types for bridging ChatGPT Web
 * ideation sessions to local agent execution environments (PI / coding agents).
 * Compatible with JSON Schema Draft-07 / Draft 2020-12 definitions.
 */

// ============================================================================
// Protocol & Transport Identifiers
// ============================================================================

export type ProtocolVersion = `${number}.${number}.${number}` | string;

export type IngestionSource =
  | 'chatgpt_web_action'
  | 'browser_extension'
  | 'clipboard_sync'
  | 'mcp_gateway'
  | 'file_sync'
  | 'cli'
  | 'manual';

export type MessageRole = 'system' | 'user' | 'assistant' | 'tool';

export interface DiscussionMessage {
  role: MessageRole;
  content: string;
  timestamp?: string; // ISO-8601 UTC
}

// ============================================================================
// Context & Provenance
// ============================================================================

export interface ContextDefinition {
  session_id: string; // UUIDv4
  conversation_id?: string;
  message_id?: string;
  timestamp: string; // ISO-8601 UTC
  source: IngestionSource;
  user_intent: string;
  decision_rationale: string;
  discussion_summary?: string;
  discussion_history?: DiscussionMessage[];
  branch_or_context?: string;
  custom_metadata?: Record<string, unknown>;
}

// ============================================================================
// Task Definition & Classification
// ============================================================================

export type TaskCategory =
  | 'feature'
  | 'bugfix'
  | 'refactor'
  | 'test'
  | 'documentation'
  | 'investigation'
  | 'migration'
  | 'chore'
  | 'security';

export type TaskPriority = 'critical' | 'high' | 'medium' | 'low';

export type TaskComplexity = 'trivial' | 'small' | 'medium' | 'large' | 'epic';

export interface TaskDefinition {
  task_id: string; // Regex: ^[a-zA-Z0-9_-]{3,64}$
  title: string;
  description: string;
  category: TaskCategory;
  priority: TaskPriority;
  complexity: TaskComplexity;
  tags?: string[];
  dependencies?: string[];
  estimated_duration_seconds?: number;
}

// ============================================================================
// Technical Constraints & Security Policies
// ============================================================================

export type SupportedOS = 'mac' | 'linux' | 'windows' | 'any';

export type ExecutionMode =
  | 'dry_run'
  | 'sandboxed'
  | 'interactive_approval'
  | 'autonomous';

export type NetworkAccessLevel = 'none' | 'local_only' | 'full';

export interface EnvironmentConstraints {
  os: SupportedOS;
  runtime: string; // e.g. "node>=20", "python>=3.11"
  cwd: string;
  env_vars?: Record<string, string>;
}

export interface SecurityPolicy {
  execution_mode: ExecutionMode;
  permitted_commands: string[];
  forbidden_commands: string[];
  allowed_file_paths: string[];
  forbidden_file_paths: string[];
  network_access?: NetworkAccessLevel;
}

export interface ExecutionLimits {
  max_execution_timeout_seconds: number;
  max_iterations: number;
  max_file_modifications: number;
  max_token_budget?: number;
}

export interface DeterministicParameters {
  temperature?: number;
  seed?: number;
  strict_mode?: boolean;
}

export interface ConstraintsDefinition {
  environment: EnvironmentConstraints;
  security_policy: SecurityPolicy;
  limits: ExecutionLimits;
  deterministic_parameters?: DeterministicParameters;
}

// ============================================================================
// File Operations
// ============================================================================

export type FileOperationType =
  | 'create'
  | 'modify'
  | 'delete'
  | 'inspect'
  | 'rename'
  | 'patch';

export interface LineRange {
  start_line: number;
  end_line: number;
}

export interface FileOperationItem {
  path: string;
  operation: FileOperationType;
  target_path?: string; // Used when renaming or moving
  description?: string;
  is_critical: boolean;
  line_ranges?: LineRange[];
  content?: string; // Whole file replacement content
  diff_patch?: string; // Unified diff format
  expected_sha256_before?: string; // 64-char hex SHA-256
  expected_sha256_after?: string;  // 64-char hex SHA-256
}

// ============================================================================
// Verification & Acceptance Criteria
// ============================================================================

export type VerificationCheckType =
  | 'command'
  | 'file_exists'
  | 'file_contains'
  | 'file_matches_regex'
  | 'http_check'
  | 'test_suite';

export interface AutomatedCheck {
  id: string;
  name: string;
  type: VerificationCheckType;
  command?: string;
  target_path?: string;
  pattern?: string;
  expected_exit_code?: number;
  expected_stdout_contains?: string[];
  expected_stdout_matches?: string;
  forbidden_stderr_contains?: string[];
  timeout_seconds?: number;
  is_blocking: boolean;
}

export interface VerificationDefinition {
  automated_checks: AutomatedCheck[];
  manual_verification_steps?: string[];
  acceptance_statement: string;
}

// ============================================================================
// Execution Options
// ============================================================================

export type NotificationChannel =
  | 'websocket'
  | 'webhook'
  | 'desktop_notification'
  | 'none';

export interface ExecutionOptionsDefinition {
  auto_rollback_on_failure?: boolean;
  notify_channel?: NotificationChannel;
  create_git_branch?: string;
}

// ============================================================================
// Top-Level Forward Task Handoff Payload
// ============================================================================

export interface TaskHandoffPayload {
  schema_version: ProtocolVersion;
  context: ContextDefinition;
  task: TaskDefinition;
  constraints: ConstraintsDefinition;
  file_operations: FileOperationItem[];
  verification: VerificationDefinition;
  execution_options?: ExecutionOptionsDefinition;
  extensions?: Record<string, unknown>;
}

// Alias for backwards compatibility
export type TaskPayload = TaskHandoffPayload;

// ============================================================================
// Bidirectional Execution Telemetry & Feedback Event
// ============================================================================

export type TaskLifecycleState =
  | 'received'
  | 'validating'
  | 'queued'
  | 'running'
  | 'waiting_confirmation'
  | 'waiting_user_input'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'blocked';

export type TaskExecutionState = TaskLifecycleState;

export interface ProgressMetrics {
  percent: number; // 0 - 100
  current_step: string;
  total_steps: number;
  completed_steps: number;
  estimated_remaining_seconds?: number;
}

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export type LogSource = 'agent' | 'stdout' | 'stderr' | 'system' | 'verifier';

export interface StructuredLog {
  timestamp: string; // ISO-8601 UTC
  level: LogLevel;
  source: LogSource;
  message: string;
  metadata?: Record<string, unknown>;
}

export type ArtifactType =
  | 'source_code'
  | 'patch'
  | 'test_report'
  | 'log'
  | 'documentation'
  | 'binary';

export interface GeneratedArtifact {
  path: string;
  type: ArtifactType;
  size_bytes: number;
  sha256?: string;
  description?: string;
}

export type CheckStatus = 'passed' | 'failed' | 'skipped' | 'timed_out';

export interface VerificationResultItem {
  step_id: string;
  name: string;
  status: CheckStatus;
  exit_code?: number;
  duration_ms: number;
  stdout_snippet?: string;
  stderr_snippet?: string;
  failure_reason?: string;
}

export type ErrorCode =
  | 'ERR_SCHEMA_VALIDATION'
  | 'ERR_SECURITY_VIOLATION'
  | 'ERR_FORBIDDEN_PATH'
  | 'ERR_FORBIDDEN_COMMAND'
  | 'ERR_TIMEOUT'
  | 'ERR_VERIFICATION_FAILED'
  | 'ERR_DEPENDENCY_MISSING'
  | 'ERR_AGENT_CRASH'
  | 'ERR_UNAUTHORIZED'
  | 'ERR_FILE_CONFLICT'
  | 'ERR_USER_CANCELLED'
  | 'ERR_INTERNAL';

export interface StructuredError {
  code: ErrorCode;
  message: string;
  stack_trace?: string;
  is_fatal: boolean;
  remediation_suggestion?: string;
}

export type UserPromptType = 'confirm' | 'select' | 'text_input' | 'secret_input';

export interface UserPromptRequest {
  prompt_id: string;
  prompt_type: UserPromptType;
  message: string;
  choices?: string[];
  default_value?: string;
  timeout_seconds?: number;
}

export interface TaskFeedbackEvent {
  schema_version: ProtocolVersion;
  event_id: string; // UUIDv4
  task_id: string;
  timestamp: string; // ISO-8601 UTC
  state: TaskLifecycleState;
  progress: ProgressMetrics;
  summary: string;
  logs?: StructuredLog[];
  artifacts?: GeneratedArtifact[];
  verification_results?: VerificationResultItem[];
  error?: StructuredError;
  user_prompt?: UserPromptRequest;
  custom_data?: Record<string, unknown>;
}
