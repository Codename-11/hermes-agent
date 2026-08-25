/**
 * User-facing labels for the established tri-state desktop.disable_gpu
 * contract. The persisted values intentionally remain auto/false/true because
 * the CLI launch bridge already maps them to detection / GPU-on / software.
 */
export const GPU_POLICY_OPTION_LABELS: Record<string, string> = {
  auto: 'Automatic',
  false: 'GPU on',
  true: 'Software rendering'
}