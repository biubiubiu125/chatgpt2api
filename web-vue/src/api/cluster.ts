import apiClient from './client'

export interface ClusterWorkerState {
  worker_id: string
  join_status: string
  online: boolean
  heartbeat_at: string
  heartbeat_age_seconds: number
  node_role: string
  run_api: boolean
  run_worker: boolean
  wireguard_ip: string
  image_base_url: string
  current_concurrency: number
  effective_concurrency: number
  remaining_capacity: number
  available_account_count: number
  available_quota: number
  pause_reason: string
  recent_error: string
  upstream_error_rate: number
  delivery_status: 'healthy' | 'unhealthy' | 'unknown' | string
  delivery_checked_at: string
  delivery_url: string
  delivery_error: string
  delivery_failures: number
  resource_snapshot?: Record<string, unknown>
}

export interface ClusterState {
  updated_at: string
  join_error?: string
  node: {
    node_role: string
    run_api: boolean
    run_worker: boolean
    worker_id: string
    wireguard_ip: string
    image_base_url: string
    cluster_id: string
  }
  queue: Record<string, any>
  workers: ClusterWorkerState[]
  runtime_health?: Record<string, any>
  accounts: Record<string, any>
  register: Record<string, any>
}

// `create` refuses a Worker number that is already registered; `rotate` revokes the
// previous credentials for that number and issues a fresh set.
export type WorkerJoinOperation = 'create' | 'rotate'

export const clusterApi = {
  state() {
    return apiClient.get<never, ClusterState>('/api/cluster/state')
  },
  createJoinFile(
    workerNo: number,
    options?: { operation?: WorkerJoinOperation; signal?: AbortSignal },
  ) {
    return apiClient.post<{ worker_no: number; operation: WorkerJoinOperation }, Blob>(
      '/api/cluster/join-file',
      { worker_no: workerNo, operation: options?.operation || 'create' },
      { responseType: 'blob', timeout: 150000, signal: options?.signal },
    )
  },
}
