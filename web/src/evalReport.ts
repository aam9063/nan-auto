/**
 * Snapshot of eval/data/report.json (the quality matrix the eval harness
 * measured, NAU-14) so the Evaluación screen can show the mockup's KPIs,
 * scatter and baselines with real, measured numbers.
 *
 * IMPORTANT: this is a build-time copy, not a live endpoint. The gateway
 * serves no endpoint carrying quality measurements, and tsconfig.json is not
 * edited to enable JSON imports. If the eval is re-run, regenerate this file
 * from the report with:
 *   node -e "const r=require('./eval/data/report.json'); console.log(JSON.stringify(r))"
 * and paste the result into EVAL_REPORT below.
 *
 * The numbers describe only the labelled sample the report itself declares
 * (prompts, completeness); nothing here is extrapolated.
 */

export interface EvalBaseline {
  quality: number
  latency_ms: number
  coverage: number
  scored_prompts: number
  model?: string
}

export interface EvalModelPoint {
  model: string
  quality: number
  latency_ms: number
  coverage: number
  scored_prompts: number
}

export interface EvalReport {
  prompts: number
  models: string[]
  complete: boolean
  missing_calls: number
  baselines: Record<string, EvalBaseline>
  debug_r3_choices: Record<string, string>
  pareto: { models: EvalModelPoint[]; policies: { policy: string; quality: number; latency_ms: number }[] }
  note: string
}

export const EVAL_REPORT: EvalReport = {
  "prompts": 28,
  "models": [
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "gemma4",
    "glm5.3",
    "glm5.3-flash",
    "hy4",
    "kimi-k3.1-pro",
    "mimo-v2.6-flash",
    "qwen3.6",
    "qwen3.8-flash",
    "qwen3.8-max"
  ],
  "complete": false,
  "missing_calls": 1,
  "baselines": {
    "R0": {
      "quality": 3.6785714285714284,
      "latency_ms": 9840.71813571453,
      "coverage": 1,
      "scored_prompts": 28,
      "model": "deepseek-v4-flash"
    },
    "R1": {
      "quality": 3.6785714285714284,
      "latency_ms": 15949.166974999442,
      "coverage": 1,
      "scored_prompts": 28,
      "model": "glm5.3"
    },
    "R2": {
      "quality": 3.6785714285714284,
      "latency_ms": 9840.71813571453,
      "coverage": 1,
      "scored_prompts": 28,
      "model": "deepseek-v4-flash"
    },
    "R3": {
      "model": "router",
      "quality": 3.5714285714285716,
      "latency_ms": 13126.324157142464,
      "coverage": 1,
      "scored_prompts": 28
    }
  },
  "pareto": {
    "models": [
      {
        "quality": 3.6785714285714284,
        "latency_ms": 9840.71813571453,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "deepseek-v4-flash"
      },
      {
        "quality": 3.0714285714285716,
        "latency_ms": 16971.214285714137,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "deepseek-v4-pro"
      },
      {
        "quality": 3.9285714285714284,
        "latency_ms": 12892.517653571174,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "gemma4"
      },
      {
        "quality": 3.6785714285714284,
        "latency_ms": 15949.166974999442,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "glm5.3"
      },
      {
        "quality": 3.2857142857142856,
        "latency_ms": 21316.977389286487,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "glm5.3-flash"
      },
      {
        "quality": 3.0357142857142856,
        "latency_ms": 20517.69414642851,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "hy4"
      },
      {
        "quality": 3.5,
        "latency_ms": 23916.316517857143,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "kimi-k3.1-pro"
      },
      {
        "quality": 3.607142857142857,
        "latency_ms": 36028.97930714166,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "mimo-v2.6-flash"
      },
      {
        "quality": 3.814814814814815,
        "latency_ms": 15580.819037036606,
        "coverage": 0.9642857142857143,
        "scored_prompts": 27,
        "model": "qwen3.6"
      },
      {
        "quality": 3.9285714285714284,
        "latency_ms": 25184.936299999597,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "qwen3.8-flash"
      },
      {
        "quality": 3.9285714285714284,
        "latency_ms": 51837.0108321425,
        "coverage": 1,
        "scored_prompts": 28,
        "model": "qwen3.8-max"
      }
    ],
    "policies": [
      {
        "policy": "R0",
        "quality": 3.6785714285714284,
        "latency_ms": 9840.71813571453
      },
      {
        "policy": "R1",
        "quality": 3.6785714285714284,
        "latency_ms": 15949.166974999442
      },
      {
        "policy": "R2",
        "quality": 3.6785714285714284,
        "latency_ms": 9840.71813571453
      },
      {
        "policy": "R3",
        "quality": 3.5714285714285716,
        "latency_ms": 13126.324157142464
      }
    ]
  },
  "debug_r3_choices": {
    "en-001": "qwen3.8-flash",
    "en-002": "qwen3.8-flash",
    "en-003": "qwen3.8-flash",
    "en-004": "qwen3.6",
    "en-005": "deepseek-v4-flash",
    "en-006": "qwen3.6",
    "en-007": "gemma4",
    "en-008": "qwen3.6",
    "en-009": "deepseek-v4-flash",
    "en-010": "deepseek-v4-flash",
    "es-001": "qwen3.8-flash",
    "es-002": "qwen3.8-flash",
    "es-003": "qwen3.6",
    "es-004": "qwen3.6",
    "es-005": "gemma4",
    "es-006": "gemma4",
    "es-007": "deepseek-v4-flash",
    "es-008": "deepseek-v4-flash",
    "es-009": "qwen3.6",
    "es-010": "deepseek-v4-flash",
    "en-011": "glm5.3",
    "en-012": "deepseek-v4-flash",
    "en-013": "qwen3.6",
    "en-014": "qwen3.6",
    "en-015": "gemma4",
    "en-016": "deepseek-v4-flash",
    "es-011": "qwen3.8-flash",
    "es-012": "gemma4"
  },
  "note": "Honest sample: 28 labelled prompts; these numbers describe this sample only, never the full distribution of requests."
}
