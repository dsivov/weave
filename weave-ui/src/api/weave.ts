import axios, { AxiosError } from 'axios'
import { backendBaseUrl, popularLabelsDefaultLimit, searchLabelsDefaultLimit } from '@/lib/constants'
import { errorMessage } from '@/lib/utils'
import { useSettingsStore } from '@/stores/settings'
import { useAuthStore } from '@/stores/state'
import { navigationService } from '@/services/navigation'

// Types
export type WeaveNodeType = {
  id: string
  labels: string[]
  properties: Record<string, any>
}

export type WeaveEdgeType = {
  id: string
  source: string
  target: string
  type: string
  properties: Record<string, any>
}

export type WeaveGraphType = {
  nodes: WeaveNodeType[]
  edges: WeaveEdgeType[]
}

export type WeaveStatus = {
  status: 'healthy'
  working_directory: string
  input_directory: string
  configuration: {
    llm_binding: string
    llm_binding_host: string
    llm_model: string
    embedding_binding: string
    embedding_binding_host: string
    embedding_model: string
    kv_storage: string
    doc_status_storage: string
    graph_storage: string
    vector_storage: string
    workspace?: string
    max_graph_nodes?: string
    enable_rerank?: boolean
    rerank_binding?: string | null
    rerank_model?: string | null
    rerank_binding_host?: string | null
    summary_language: string
    force_llm_summary_on_merge: boolean
    max_parallel_insert: number
    max_async: number
    embedding_func_max_async: number
    embedding_batch_num: number
    cosine_threshold: number
    min_rerank_score: number
    related_chunk_number: number
  }
  update_status?: Record<string, any>
  core_version?: string
  api_version?: string
  auth_mode?: 'enabled' | 'disabled'
  pipeline_busy: boolean
  keyed_locks?: {
    process_id: number
    cleanup_performed: {
      mp_cleaned: number
      async_cleaned: number
    }
    current_status: {
      total_mp_locks: number
      pending_mp_cleanup: number
      total_async_locks: number
      pending_async_cleanup: number
    }
  }
  webui_title?: string
  webui_description?: string
}

export type WeaveDocumentsScanProgress = {
  is_scanning: boolean
  current_file: string
  indexed_count: number
  total_files: number
  progress: number
}

/**
 * Specifies the retrieval mode:
 * - "naive": Performs a basic search without advanced techniques.
 * - "local": Focuses on context-dependent information.
 * - "global": Utilizes global knowledge.
 * - "hybrid": Combines local and global retrieval methods.
 * - "mix": Integrates knowledge graph and vector retrieval.
 * - "bypass": Bypasses knowledge retrieval and directly uses the LLM.
 */
export type QueryMode = 'naive' | 'local' | 'global' | 'hybrid' | 'mix' | 'bypass' | 'cgr3'

export type Message = {
  role: 'user' | 'assistant' | 'system'
  content: string
  thinkingContent?: string
  displayContent?: string
  thinkingTime?: number | null
}

export type QueryRequest = {
  query: string
  /** Specifies the retrieval mode. */
  mode: QueryMode
  /** If True, only returns the retrieved context without generating a response. */
  only_need_context?: boolean
  /** If True, only returns the generated prompt without producing a response. */
  only_need_prompt?: boolean
  /** Defines the response format. Examples: 'Multiple Paragraphs', 'Single Paragraph', 'Bullet Points'. */
  response_type?: string
  /** If True, enables streaming output for real-time responses. */
  stream?: boolean
  /** Number of top items to retrieve. Represents entities in 'local' mode and relationships in 'global' mode. */
  top_k?: number
  /** Maximum number of text chunks to retrieve and keep after reranking. */
  chunk_top_k?: number
  /** Maximum number of tokens allocated for entity context in unified token control system. */
  max_entity_tokens?: number
  /** Maximum number of tokens allocated for relationship context in unified token control system. */
  max_relation_tokens?: number
  /** Maximum total tokens budget for the entire query context (entities + relations + chunks + system prompt). */
  max_total_tokens?: number
  /**
   * Stores past conversation history to maintain context.
   * Format: [{"role": "user/assistant", "content": "message"}].
   */
  conversation_history?: Message[]
  /** Number of complete conversation turns (user-assistant pairs) to consider in the response context. */
  history_turns?: number
  /** User-provided prompt for the query. If provided, this will be used instead of the default value from prompt template. */
  user_prompt?: string
  /** Enable reranking for retrieved text chunks. If True but no rerank model is configured, a warning will be issued. Default is True. */
  enable_rerank?: boolean
}

export type QueryResponse = {
  response: string
}

export type EntityUpdateResponse = {
  status: string
  message: string
  data: Record<string, any>
  operation_summary?: {
    merged: boolean
    merge_status: 'success' | 'failed' | 'not_attempted'
    merge_error: string | null
    operation_status: 'success' | 'partial_success' | 'failure'
    target_entity: string | null
    final_entity?: string | null
    renamed?: boolean
  }
}

export type DocActionResponse = {
  status: 'success' | 'partial_success' | 'failure' | 'duplicated'
  message: string
  track_id?: string
}

export type ScanResponse = {
  status: 'scanning_started'
  message: string
  track_id: string
}

export type ReprocessFailedResponse = {
  status: 'reprocessing_started'
  message: string
  track_id: string
}

export type DeleteDocResponse = {
  status: 'deletion_started' | 'busy' | 'not_allowed'
  message: string
  doc_id: string
}

export type DocStatus = 'pending' | 'processing' | 'preprocessed' | 'processed' | 'failed'

export type DocStatusResponse = {
  id: string
  content_summary: string
  content_length: number
  status: DocStatus
  created_at: string
  updated_at: string
  track_id?: string
  chunks_count?: number
  error_msg?: string
  metadata?: Record<string, any>
  file_path: string
}

export type DocsStatusesResponse = {
  statuses: Record<DocStatus, DocStatusResponse[]>
}

export type TrackStatusResponse = {
  track_id: string
  documents: DocStatusResponse[]
  total_count: number
  status_summary: Record<string, number>
}

export type DocumentsRequest = {
  status_filter?: DocStatus | null
  page: number
  page_size: number
  sort_field: 'created_at' | 'updated_at' | 'id' | 'file_path'
  sort_direction: 'asc' | 'desc'
}

export type PaginationInfo = {
  page: number
  page_size: number
  total_count: number
  total_pages: number
  has_next: boolean
  has_prev: boolean
}

export type PaginatedDocsResponse = {
  documents: DocStatusResponse[]
  pagination: PaginationInfo
  status_counts: Record<string, number>
}

export type StatusCountsResponse = {
  status_counts: Record<string, number>
}

export type AuthStatusResponse = {
  auth_configured: boolean
  access_token?: string
  token_type?: string
  auth_mode?: 'enabled' | 'disabled'
  message?: string
  core_version?: string
  api_version?: string
  webui_title?: string
  webui_description?: string
}

export type PipelineStatusResponse = {
  autoscanned: boolean
  busy: boolean
  job_name: string
  job_start?: string
  docs: number
  batchs: number
  cur_batch: number
  request_pending: boolean
  cancellation_requested?: boolean
  latest_message: string
  history_messages?: string[]
  update_status?: Record<string, any>
}

export type LoginResponse = {
  access_token: string
  token_type: string
  auth_mode?: 'enabled' | 'disabled'  // Authentication mode identifier
  message?: string                    // Optional message
  core_version?: string
  api_version?: string
  webui_title?: string
  webui_description?: string
}

export const InvalidApiKeyError = 'Invalid API Key'
export const RequireApiKeError = 'API Key required'

// Axios instance
const axiosInstance = axios.create({
  baseURL: backendBaseUrl,
  headers: {
    'Content-Type': 'application/json'
  }
})

// ========== Token Management ==========
// Prevent multiple requests from triggering token refresh simultaneously
let isRefreshingGuestToken = false;
let refreshTokenPromise: Promise<string> | null = null;

// Silent refresh for guest token
const silentRefreshGuestToken = async (): Promise<string> => {
  // If already refreshing, return the same Promise
  if (isRefreshingGuestToken && refreshTokenPromise) {
    return refreshTokenPromise;
  }

  isRefreshingGuestToken = true;
  refreshTokenPromise = (async () => {
    try {
      // Call /auth-status to get new guest token
      const response = await axios.get('/auth-status', {
        baseURL: backendBaseUrl,
        // This request must skip the interceptor to avoid adding expired token
        headers: { 'X-Skip-Interceptor': 'true' }
      });

      if (response.data.access_token && !response.data.auth_configured) {
        const newToken = response.data.access_token;
        // Update localStorage
        localStorage.setItem('WEAVE-API-TOKEN', newToken);
        // Update auth state
        useAuthStore.getState().login(
          newToken,
          true,
          response.data.core_version,
          response.data.api_version,
          response.data.webui_title || null,
          response.data.webui_description || null
        );
        return newToken;
      } else {
        throw new Error('Failed to get guest token');
      }
    } finally {
      isRefreshingGuestToken = false;
      refreshTokenPromise = null;
    }
  })();

  return refreshTokenPromise;
};

// Interceptor: add api key and check authentication
axiosInstance.interceptors.request.use((config) => {
  // Skip interceptor for token refresh requests
  if (config.headers['X-Skip-Interceptor']) {
    delete config.headers['X-Skip-Interceptor'];
    return config;
  }

  const settings = useSettingsStore.getState()
  const apiKey = settings.apiKey
  const workspace = settings.workspace
  const token = localStorage.getItem('WEAVE-API-TOKEN');

  // Always include token if it exists, regardless of path
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    config.headers['X-API-Key'] = apiKey
  }
  if (workspace) {
    config.headers['WEAVE-WORKSPACE'] = workspace
  }
  return config
})

// Interceptor：handle token renewal and authentication errors
axiosInstance.interceptors.response.use(
  (response) => {
    // ========== Check for new token from backend ==========
    const newToken = response.headers['x-new-token'];
    if (newToken) {
      localStorage.setItem('WEAVE-API-TOKEN', newToken);

      // Optional: log in development mode
      if (import.meta.env.DEV) {
        console.log('[Auth] Token auto-renewed by backend');
      }

      // Update auth state with renewal tracking
      try {
        const payload = JSON.parse(atob(newToken.split('.')[1]));
        const authStore = useAuthStore.getState();
        if (authStore.isAuthenticated) {
          // Track token renewal time and expiration
          const renewalTime = Date.now();
          const expiresAt = payload.exp ? payload.exp * 1000 : 0;
          authStore.setTokenRenewal(renewalTime, expiresAt);

          // The identity the *renewed* token carries (W21).
          //
          // This used to be a comment saying "we'll skip username update as
          // it's rare". Rarity was never the issue: the server enforces the
          // role in the token, so a store that does not follow the token shows
          // a role nobody is checking against. Both fields, every renewal.
          authStore.setIdentity(payload.sub ?? null, payload.role ?? null);
        }
      } catch (error) {
        console.warn('[Auth] Failed to parse renewed token:', error);
      }
    }
    // ========== End of token renewal check ==========

    return response;
  },
  async (error: AxiosError) => {
    if (error.response) {
      if (error.response?.status === 401) {
        const originalRequest = error.config;

        // 1. For login API, throw error directly
        if (originalRequest?.url?.includes('/login')) {
          throw error;
        }

        // 2. Prevent infinite retry
        if (originalRequest && (originalRequest as any)._retry) {
          navigationService.navigateToLogin();
          return Promise.reject(new Error('Authentication required'));
        }

        // 3. Check if in guest mode
        const authStore = useAuthStore.getState();
        const currentToken = localStorage.getItem('WEAVE-API-TOKEN');
        const isGuest = currentToken && authStore.isGuestMode;

        // 4. Guest mode: silent refresh and retry
        if (isGuest && originalRequest) {
          try {
            const newToken = await silentRefreshGuestToken();

            // Mark as retried to prevent infinite loop
            (originalRequest as any)._retry = true;

            // Update token in request headers
            originalRequest.headers['Authorization'] = `Bearer ${newToken}`;

            // Retry original request
            return axiosInstance(originalRequest);
          } catch (refreshError) {
            console.error('Failed to refresh guest token:', refreshError);
            // Refresh failed, navigate to login
            navigationService.navigateToLogin();
            return Promise.reject(new Error('Failed to refresh authentication'));
          }
        }

        // 5. Non-guest mode: navigate to login page
        navigationService.navigateToLogin();
        return Promise.reject(new Error('Authentication required'));
      }
      // The message stays a formatted one-liner, because plenty of callers just
      // surface it. But the parsed response rides along: a caller that needs to
      // *act* on a status cannot recover one from a formatted string, and ends
      // up treating a specific answer as a generic failure. The diagram save is
      // the case that bit — a 422 there is the server asking for a sign-off, and
      // losing it turned "sign this off to share it" into "save failed".
      const err = new Error(
        `${error.response.status} ${error.response.statusText}\n${JSON.stringify(
          error.response.data
        )}\n${error.config?.url}`
      ) as Error & { status?: number; response?: AxiosError['response'] }
      err.status = error.response.status
      err.response = error.response
      throw err
    }
    throw error
  }
)

// API methods
export const queryGraphs = async (
  label: string,
  maxDepth: number,
  maxNodes: number
): Promise<WeaveGraphType> => {
  const response = await axiosInstance.get(`/graphs?label=${encodeURIComponent(label)}&max_depth=${maxDepth}&max_nodes=${maxNodes}`)
  return response.data
}

export const getGraphLabels = async (): Promise<string[]> => {
  const response = await axiosInstance.get('/graph/label/list')
  return response.data
}

export const getPopularLabels = async (limit: number = popularLabelsDefaultLimit): Promise<string[]> => {
  const response = await axiosInstance.get(`/graph/label/popular?limit=${limit}`)
  return response.data
}

export const searchLabels = async (query: string, limit: number = searchLabelsDefaultLimit): Promise<string[]> => {
  const response = await axiosInstance.get(`/graph/label/search?q=${encodeURIComponent(query)}&limit=${limit}`)
  return response.data
}

export const checkHealth = async (): Promise<
  WeaveStatus | { status: 'error'; message: string }
> => {
  try {
    const response = await axiosInstance.get('/health')
    return response.data
  } catch (error) {
    return {
      status: 'error',
      message: errorMessage(error)
    }
  }
}

export const getDocuments = async (): Promise<DocsStatusesResponse> => {
  const response = await axiosInstance.get('/documents')
  return response.data
}

export const scanNewDocuments = async (): Promise<ScanResponse> => {
  const response = await axiosInstance.post('/documents/scan')
  return response.data
}

export const reprocessFailedDocuments = async (): Promise<ReprocessFailedResponse> => {
  const response = await axiosInstance.post('/documents/reprocess_failed')
  return response.data
}

export const getDocumentsScanProgress = async (): Promise<WeaveDocumentsScanProgress> => {
  const response = await axiosInstance.get('/documents/scan-progress')
  return response.data
}

export const queryText = async (request: QueryRequest): Promise<QueryResponse> => {
  const response = await axiosInstance.post('/query', request)
  return response.data
}

// Raw retrieval data incl. actual chunk text — for the Chunk Inspector.
export type RetrievedChunk = { chunk_id: string; content: string; file_path?: string; reference_id?: string }
export type QueryDataResult = {
  entities: any[]
  relationships: any[]
  chunks: RetrievedChunk[]
  references: { reference_id: string; file_path?: string }[]
}
export const queryDataChunks = async (
  query: string,
  mode: string = 'mix',
  opts: { top_k?: number; chunk_top_k?: number } = {}
): Promise<QueryDataResult> => {
  const res = await axiosInstance.post('/query/data', {
    query,
    mode,
    top_k: opts.top_k ?? 20,
    chunk_top_k: opts.chunk_top_k ?? 10,
    include_references: true,
    include_chunk_content: true
  })
  return (res.data?.data ?? res.data) as QueryDataResult
}

export const queryTextStream = async (
  request: QueryRequest,
  onChunk: (chunk: string) => void,
  onError?: (error: string) => void
) => {
  const settings = useSettingsStore.getState();
  const apiKey = settings.apiKey;
  const workspace = settings.workspace;
  const token = localStorage.getItem('WEAVE-API-TOKEN');
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    'Accept': 'application/x-ndjson',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  }
  if (workspace) {
    headers['WEAVE-WORKSPACE'] = workspace;
  }

  try {
    const response = await fetch(`${backendBaseUrl}/query/stream`, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      // Handle 401 Unauthorized error specifically
      if (response.status === 401) {
        // Check if in guest mode
        const authStore = useAuthStore.getState();
        const currentToken = localStorage.getItem('WEAVE-API-TOKEN');
        const isGuest = currentToken && authStore.isGuestMode;

        if (isGuest) {
          try {
            // Silent refresh token for guest mode
            const newToken = await silentRefreshGuestToken();

            // Retry stream request with new token
            const retryHeaders = { ...headers };
            retryHeaders['Authorization'] = `Bearer ${newToken}`;

            const retryResponse = await fetch(`${backendBaseUrl}/query/stream`, {
              method: 'POST',
              headers: retryHeaders,
              body: JSON.stringify(request),
            });

            if (!retryResponse.ok) {
              throw new Error(`HTTP error! status: ${retryResponse.status}`);
            }

            // Retry successful, process stream response
            // Re-execute the stream processing logic with retryResponse
            if (!retryResponse.body) {
              throw new Error('Response body is null');
            }

            const reader = retryResponse.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
              const { done, value } = await reader.read();
              if (done) break;

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split('\n');
              buffer = lines.pop() || '';

              for (const line of lines) {
                if (line.trim()) {
                  try {
                    const parsed = JSON.parse(line);
                    if (parsed.response) {
                      onChunk(parsed.response);
                    } else if (parsed.error) {
                      onError?.(parsed.error);
                    }
                  } catch (parseError) {
                    console.error('Failed to parse JSON:', parseError, 'Line:', line);
                    onError?.(`JSON parse error: ${parseError}`);
                  }
                }
              }
            }

            // Process any remaining data in buffer
            if (buffer.trim()) {
              try {
                const parsed = JSON.parse(buffer);
                if (parsed.response) {
                  onChunk(parsed.response);
                } else if (parsed.error) {
                  onError?.(parsed.error);
                }
              } catch (parseError) {
                console.error('Failed to parse final buffer:', parseError);
              }
            }

            return; // Successfully completed retry
          } catch (refreshError) {
            console.error('Failed to refresh guest token for streaming:', refreshError);
            navigationService.navigateToLogin();
            throw new Error('Failed to refresh authentication');
          }
        }

        // Non-guest mode: navigate to login page
        navigationService.navigateToLogin();

        // Create a specific authentication error
        const authError = new Error('Authentication required');
        throw authError;
      }

      // Handle other common HTTP errors with specific messages
      let errorBody = 'Unknown error';
      try {
        errorBody = await response.text(); // Try to get error details from body
      } catch { /* ignore */ }

      // Format error message similar to axios interceptor for consistency
      const url = `${backendBaseUrl}/query/stream`;
      throw new Error(
        `${response.status} ${response.statusText}\n${JSON.stringify(
          { error: errorBody }
        )}\n${url}`
      );
    }

    if (!response.body) {
      throw new Error('Response body is null');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break; // Stream finished
      }

      // Decode the chunk and add to buffer
      buffer += decoder.decode(value, { stream: true }); // stream: true handles multi-byte chars split across chunks

      // Process complete lines (NDJSON)
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // Keep potentially incomplete line in buffer

      for (const line of lines) {
        if (line.trim()) {
          try {
            const parsed = JSON.parse(line);
            if (parsed.response) {
              onChunk(parsed.response);
            } else if (parsed.error && onError) {
              onError(parsed.error);
            }
          } catch (error) {
            console.error('Error parsing stream chunk:', line, error);
            if (onError) onError(`Error parsing server response: ${line}`);
          }
        }
      }
    }

    // Process any remaining data in the buffer after the stream ends
    if (buffer.trim()) {
      try {
        const parsed = JSON.parse(buffer);
        if (parsed.response) {
          onChunk(parsed.response);
        } else if (parsed.error && onError) {
          onError(parsed.error);
        }
      } catch (error) {
        console.error('Error parsing final chunk:', buffer, error);
        if (onError) onError(`Error parsing final server response: ${buffer}`);
      }
    }

  } catch (error) {
    const message = errorMessage(error);

    // Check if this is an authentication error
    if (message === 'Authentication required') {
      // Already navigated to login page in the response.status === 401 block
      console.error('Authentication required for stream request');
      if (onError) {
        onError('Authentication required');
      }
      return; // Exit early, no need for further error handling
    }

    // Check for specific HTTP error status codes in the error message
    const statusCodeMatch = message.match(/^(\d{3})\s/);
    if (statusCodeMatch) {
      const statusCode = parseInt(statusCodeMatch[1], 10);

      // Handle specific status codes with user-friendly messages
      let userMessage = message;

      switch (statusCode) {
      case 403:
        userMessage = 'You do not have permission to access this resource (403 Forbidden)';
        console.error('Permission denied for stream request:', message);
        break;
      case 404:
        userMessage = 'The requested resource does not exist (404 Not Found)';
        console.error('Resource not found for stream request:', message);
        break;
      case 429:
        userMessage = 'Too many requests, please try again later (429 Too Many Requests)';
        console.error('Rate limited for stream request:', message);
        break;
      case 500:
      case 502:
      case 503:
      case 504:
        userMessage = `Server error, please try again later (${statusCode})`;
        console.error('Server error for stream request:', message);
        break;
      default:
        console.error('Stream request failed with status code:', statusCode, message);
      }

      if (onError) {
        onError(userMessage);
      }
      return;
    }

    // Handle network errors (like connection refused, timeout, etc.)
    if (message.includes('NetworkError') ||
        message.includes('Failed to fetch') ||
        message.includes('Network request failed')) {
      console.error('Network error for stream request:', message);
      if (onError) {
        onError('Network connection error, please check your internet connection');
      }
      return;
    }

    // Handle JSON parsing errors during stream processing
    if (message.includes('Error parsing') || message.includes('SyntaxError')) {
      console.error('JSON parsing error in stream:', message);
      if (onError) {
        onError('Error processing response data');
      }
      return;
    }

    // Handle other errors
    console.error('Unhandled stream error:', message);
    if (onError) {
      onError(message);
    } else {
      console.error('No error handler provided for stream error:', message);
    }
  }
};

export const insertText = async (text: string): Promise<DocActionResponse> => {
  const response = await axiosInstance.post('/documents/text', { text })
  return response.data
}

export const insertTexts = async (texts: string[]): Promise<DocActionResponse> => {
  const response = await axiosInstance.post('/documents/texts', { texts })
  return response.data
}

export const uploadDocument = async (
  file: File,
  onUploadProgress?: (percentCompleted: number) => void
): Promise<DocActionResponse> => {
  const formData = new FormData()
  formData.append('file', file)

  const response = await axiosInstance.post('/documents/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    },
    // prettier-ignore
    onUploadProgress:
      onUploadProgress !== undefined
        ? (progressEvent) => {
          const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total!)
          onUploadProgress(percentCompleted)
        }
        : undefined
  })
  return response.data
}

export const batchUploadDocuments = async (
  files: File[],
  onUploadProgress?: (fileName: string, percentCompleted: number) => void
): Promise<DocActionResponse[]> => {
  return await Promise.all(
    files.map(async (file) => {
      return await uploadDocument(file, (percentCompleted) => {
        onUploadProgress?.(file.name, percentCompleted)
      })
    })
  )
}

export const clearDocuments = async (): Promise<DocActionResponse> => {
  const response = await axiosInstance.delete('/documents')
  return response.data
}

export const clearCache = async (): Promise<{
  status: 'success' | 'fail'
  message: string
}> => {
  const response = await axiosInstance.post('/documents/clear_cache', {})
  return response.data
}

export const deleteDocuments = async (
  docIds: string[],
  deleteFile: boolean = false,
  deleteLLMCache: boolean = false
): Promise<DeleteDocResponse> => {
  const response = await axiosInstance.delete('/documents/delete_document', {
    data: { doc_ids: docIds, delete_file: deleteFile, delete_llm_cache: deleteLLMCache }
  })
  return response.data
}

export const getAuthStatus = async (): Promise<AuthStatusResponse> => {
  try {
    // Add a timeout to the request to prevent hanging
    const response = await axiosInstance.get('/auth-status', {
      timeout: 5000, // 5 second timeout
      headers: {
        'Accept': 'application/json' // Explicitly request JSON
      }
    });

    // Check if response is HTML (which indicates a redirect or wrong endpoint)
    // `AxiosHeaders` values are a union (string | number | boolean | string[]),
    // so `.includes` is not available on the declared type even though this one
    // is always a string at runtime. Normalised rather than cast, so a header
    // that genuinely arrives as an array still answers the question correctly.
    const contentType = String(response.headers['content-type'] ?? '');
    if (contentType.includes('text/html')) {
      console.warn('Received HTML response instead of JSON for auth-status endpoint');
      return {
        auth_configured: true,
        auth_mode: 'enabled'
      };
    }

    // Strict validation of the response data
    if (response.data &&
        typeof response.data === 'object' &&
        'auth_configured' in response.data &&
        typeof response.data.auth_configured === 'boolean') {

      // For unconfigured auth, ensure we have an access token
      if (!response.data.auth_configured) {
        if (response.data.access_token && typeof response.data.access_token === 'string') {
          return response.data;
        } else {
          console.warn('Auth not configured but no valid access token provided');
        }
      } else {
        // For configured auth, just return the data
        return response.data;
      }
    }

    // If response data is invalid but we got a response, log it
    console.warn('Received invalid auth status response:', response.data);

    // Default to auth configured if response is invalid
    return {
      auth_configured: true,
      auth_mode: 'enabled'
    };
  } catch (error) {
    // If the request fails, assume authentication is configured
    console.error('Failed to get auth status:', errorMessage(error));
    return {
      auth_configured: true,
      auth_mode: 'enabled'
    };
  }
}

export const getPipelineStatus = async (): Promise<PipelineStatusResponse> => {
  const response = await axiosInstance.get('/documents/pipeline_status')
  return response.data
}

export const cancelPipeline = async (): Promise<{
  status: 'cancellation_requested' | 'not_busy'
  message: string
}> => {
  const response = await axiosInstance.post('/documents/cancel_pipeline')
  return response.data
}

export const loginToServer = async (username: string, password: string): Promise<LoginResponse> => {
  const formData = new FormData();
  formData.append('username', username);
  formData.append('password', password);

  const response = await axiosInstance.post('/login', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  });

  return response.data;
}

/**
 * Updates an entity's properties in the knowledge graph
 * @param entityName The name of the entity to update
 * @param updatedData Dictionary containing updated attributes
 * @param allowRename Whether to allow renaming the entity (default: false)
 * @param allowMerge Whether to merge into an existing entity when renaming to a duplicate name
 * @returns Promise with the updated entity information
 */
export const updateEntity = async (
  entityName: string,
  updatedData: Record<string, any>,
  allowRename: boolean = false,
  allowMerge: boolean = false
): Promise<EntityUpdateResponse> => {
  const response = await axiosInstance.post('/graph/entity/edit', {
    entity_name: entityName,
    updated_data: updatedData,
    allow_rename: allowRename,
    allow_merge: allowMerge
  })
  return response.data
}

/**
 * Updates a relation's properties in the knowledge graph
 * @param sourceEntity The source entity name
 * @param targetEntity The target entity name
 * @param updatedData Dictionary containing updated attributes
 * @returns Promise with the updated relation information
 */
export const updateRelation = async (
  sourceEntity: string,
  targetEntity: string,
  updatedData: Record<string, any>
): Promise<DocActionResponse> => {
  const response = await axiosInstance.post('/graph/relation/edit', {
    source_id: sourceEntity,
    target_id: targetEntity,
    updated_data: updatedData
  })
  return response.data
}

/**
 * Checks if an entity name already exists in the knowledge graph
 * @param entityName The entity name to check
 * @returns Promise with boolean indicating if the entity exists
 */
export const checkEntityNameExists = async (entityName: string): Promise<boolean> => {
  try {
    const response = await axiosInstance.get(`/graph/entity/exists?name=${encodeURIComponent(entityName)}`)
    return response.data.exists
  } catch (error) {
    console.error('Error checking entity name:', error)
    return false
  }
}

/** A decision (rc-bearing edge) attached to a node — the "why" recorded on the graph. */
export type EntityDecision = {
  src_id: string
  tgt_id: string
  keywords?: string
  relation_context: {
    decision_trace?: string
    approved_by?: string
    approved_via?: string
    policy_ref?: string
    valid_until?: string
    confidence_score?: number
  }
}

/** The decision-bearing edges attached to an entity (Weave mode). Empty otherwise. */
export const getEntityDecisions = async (entityName: string): Promise<EntityDecision[]> => {
  try {
    const r = await axiosInstance.get(
      `/graph/entity/edges-with-context?entity_name=${encodeURIComponent(entityName)}`
    )
    const edges = (r.data?.edges || []) as EntityDecision[]
    return edges.filter((e) => e?.relation_context?.decision_trace)
  } catch {
    return []
  }
}

// Source text chunks an entity was extracted from (graph node → chunks viewer)
export type EntityChunk = { chunk_id: string; content: string; file_path?: string; chunk_order_index?: number }
export const getEntityChunks = async (name: string, limit = 20): Promise<EntityChunk[]> => {
  const res = await axiosInstance.get('/graph/entity/chunks', { params: { name, limit } })
  return (res.data?.chunks ?? []) as EntityChunk[]
}

/**
 * Get the processing status of documents by tracking ID
 * @param trackId The tracking ID returned from upload, text, or texts endpoints
 * @returns Promise with the track status response containing documents and summary
 */
export const getTrackStatus = async (trackId: string): Promise<TrackStatusResponse> => {
  const response = await axiosInstance.get(`/documents/track_status/${encodeURIComponent(trackId)}`)
  return response.data
}

/**
 * Get documents with pagination support
 * @param request The pagination request parameters
 * @returns Promise with paginated documents response
 */
export const getDocumentsPaginated = async (request: DocumentsRequest): Promise<PaginatedDocsResponse> => {
  const response = await axiosInstance.post('/documents/paginated', request)
  return response.data
}

/**
 * Get counts of documents by status
 * @returns Promise with status counts response
 */
export const getDocumentStatusCounts = async (): Promise<StatusCountsResponse> => {
  const response = await axiosInstance.get('/documents/status_counts')
  return response.data
}

export const listWorkspaces = async (): Promise<string[]> => {
  const response = await axiosInstance.get('/workspaces')
  return response.data.workspaces
}

// CGR3 (Weave Retrieve-Rank-Reason) query
export type CGR3QueryRequest = {
  query: string
  mode?: 'local' | 'global' | 'hybrid' | 'naive' | 'mix'
  max_iterations?: number
  top_k?: number
  include_references?: boolean
}

export type CGR3QueryResponse = {
  response: string
  references?: Array<{
    id: string
    source_id: string
    content: string
  }>
}

export const queryCGR3 = async (request: CGR3QueryRequest): Promise<CGR3QueryResponse> => {
  const response = await axiosInstance.post('/cgr3/query', request)
  return response.data
}

// ─────────────────────────────────────────────────────────────────────────────
// Rules & Ontology (Weave governance) — workspace-scoped
// ─────────────────────────────────────────────────────────────────────────────

export type RuleInfo = { name: string; priority: number }
export type RulesSummary = {
  workspace: string
  exists: boolean
  enabled: boolean
  version?: number
  model_id?: string | null
  updated_at?: number | null
  concepts: string[]
  rules: RuleInfo[]
  dsl?: string
  concepts_map?: Record<string, string[]>
}

export const getRules = async (): Promise<RulesSummary> =>
  (await axiosInstance.get('/rules')).data

export const setRules = async (
  dsl: string,
  concepts: Record<string, string[]>,
  enabled = true
): Promise<RulesSummary> =>
  (await axiosInstance.post('/rules', { dsl, concepts, enabled })).data

export const toggleRules = async (enabled: boolean): Promise<RulesSummary> =>
  (await axiosInstance.post('/rules/toggle', { enabled })).data

export const deleteRules = async (): Promise<{ deleted: boolean; workspace: string }> =>
  (await axiosInstance.delete('/rules')).data

export type RuleEvaluateRequest = {
  src: string
  tgt: string
  relation_type: string
  relation_context: Record<string, any>
  as_of?: string
}
export type RuleEvaluateResponse = {
  active: boolean
  outcome?: string | null
  audit?: Record<string, any> | null
  triggered: Array<Record<string, any>>
  warnings: string[]
  notes: string[]
}
export const evaluateRules = async (
  req: RuleEvaluateRequest
): Promise<RuleEvaluateResponse> =>
  (await axiosInstance.post('/rules/evaluate', req)).data

export type RuleGenerateRequest = {
  policy: string
  concepts?: Record<string, string[]>
  use_stored_concepts?: boolean
  max_repairs?: number
  save?: boolean
}
export type RuleGenerateResponse = {
  valid: boolean
  dsl: string
  concepts: Record<string, string[]>
  fixtures: Array<Record<string, any>>
  dry_run: Array<Record<string, any>>
  explanation: string
  errors: string[]
  attempts: number
  saved: boolean
}
export const generateRules = async (
  req: RuleGenerateRequest
): Promise<RuleGenerateResponse> =>
  (await axiosInstance.post('/rules/generate', req)).data

// -- Ontology -----------------------------------------------------------------

export type OntologyProperty = {
  name: string
  kind: string
  required?: boolean
  description?: string
  enum_values?: string[] | null
  minimum?: number | null
  maximum?: number | null
}
export type OntologyObjectType = {
  name: string
  description?: string
  properties: OntologyProperty[]
}
export type OntologyLinkType = {
  name: string
  source_types: string[]
  target_types: string[]
  cardinality: string
  description?: string
  properties?: OntologyProperty[]
}
export type OntologyDoc = {
  name: string
  version?: number
  object_types: OntologyObjectType[]
  link_types: OntologyLinkType[]
}
export type OntologySummary = {
  workspace: string
  exists: boolean
  name?: string | null
  version?: number | null
  updated_at?: number | null
  object_types: Array<{ name: string; description?: string; properties: Array<{ name: string; kind: string; required?: boolean }> }>
  link_types: Array<{ name: string; source_types: string[]; target_types: string[]; cardinality: string; property_count?: number }>
  lint: string[]
  ontology?: OntologyDoc | null
}

export const getOntology = async (): Promise<OntologySummary> =>
  (await axiosInstance.get('/ontology')).data

export const setOntology = async (ontology: OntologyDoc): Promise<OntologySummary> =>
  (await axiosInstance.post('/ontology', { ontology })).data

export const deleteOntology = async (): Promise<{ deleted: boolean; workspace: string }> =>
  (await axiosInstance.delete('/ontology')).data

export type OntologyGenerateRequest = {
  description: string
  extend?: boolean
  max_repairs?: number
  save?: boolean
}
export type OntologyGenerateResponse = {
  valid: boolean
  ontology: OntologyDoc
  lint: string[]
  samples: Record<string, any>
  dry_run: Record<string, any>
  explanation: string
  errors: string[]
  attempts: number
  saved: boolean
}
export const generateOntology = async (
  req: OntologyGenerateRequest
): Promise<OntologyGenerateResponse> =>
  (await axiosInstance.post('/ontology/generate', req)).data

export type OntologyValidateResponse = {
  exists: boolean
  ok?: boolean
  total?: number
  conforming?: number
  violations?: number
  unknown_types?: string[]
  by_status?: Record<string, number>
  policy?: string
  items?: Array<{ kind: string; ref: string; status: string; ok: boolean; errors: string[]; warnings: string[] }>
}
export const validateExtraction = async (
  entities: Array<Record<string, any>>,
  relations: Array<Record<string, any>>,
  closedWorld = false
): Promise<OntologyValidateResponse> =>
  (await axiosInstance.post('/ontology/validate', { entities, relations, closed_world: closedWorld })).data

/* ---- Conversational onboarding (Get Started tab) ---------------------------- */

export type OnboardChatMessage = { role: string; content: string }
export type OnboardFirstCR = { id: string; title: string; description: string }
export type OnboardProposal = {
  workspace?: string
  brief: string
  description: string
  policy?: string | null
  roles: string[]
  object_types_preview: string[]
  rules_preview: string[]
  first_cr?: OnboardFirstCR | null
  backfill?: Record<string, any>
}
export type OnboardChatResponse = {
  assistant: string
  ready: boolean
  proposal: OnboardProposal | null
}
export type OnboardApplyResponse = {
  workspace: string
  ontology: { valid: boolean; saved: boolean; object_types: string[] }
  rules?: { valid: boolean; saved: boolean; errors: string[] } | null
  roles_seeded: string[]
  first_cr?: { id: string; title: string } | null
  brief_id?: string | null
  bootstrap: {
    mcp_config: Record<string, any>
    playbook_url: string
    manifest_url: string
    backfill: { script_url: string; cmd: string; when: string }
    next_steps: string[]
  }
}

export const onboardChat = async (
  messages: OnboardChatMessage[],
  repoPresent = false
): Promise<OnboardChatResponse> =>
  (await axiosInstance.post('/onboard/chat', { messages, repo_present: repoPresent })).data

export const onboardApply = async (proposal: OnboardProposal): Promise<OnboardApplyResponse> =>
  (await axiosInstance.post('/onboard/apply', { proposal })).data

// ── Team-vocabulary wizard (P4) ──────────────────────────────────────────────
//
// The wizard installs governance by signing ledger versions, so `apply` returns
// versions and sign-offs rather than a success flag — and `restart_required` is
// part of the contract because "no restart" is the promise A8 makes.

export interface WizardTemplate {
  id: string
  title: string
  when_to_use: string
}

export interface WizardQuestion {
  id: string
  prompt: string
  kind: 'one' | 'multi' | 'bool'
  options?: string[]
  default?: unknown
}

export interface WizardPlan {
  template: string
  title: string
  when_to_use: string
  questions: WizardQuestion[]
  installs: { rbac: string[]; lifecycle: string[] }
  kinds: string[]
}

export interface WizardDiff {
  kind: string
  artifact_id: string
  from_version: number | null
  to_version: number
  behaviour_changed: boolean
  delta: { before: unknown; after: unknown }
  [key: string]: unknown
}

export interface WizardProposal {
  workspace: string
  template: string
  diffs: WizardDiff[]
  count: number
}

export interface WizardApplied {
  kind: string
  artifact_id: string
  version: number
  sign_off: { approver: string; reason: string; at: string; role?: string }
}

export interface WizardApplyResponse {
  workspace: string
  applied: WizardApplied[]
  count: number
  restart_required: boolean
}

export const wizardTemplates = async (): Promise<{ templates: WizardTemplate[] }> =>
  (await axiosInstance.get('/wizard/templates')).data

export const wizardSession = async (template: string): Promise<WizardPlan> =>
  (await axiosInstance.post('/wizard/session', { template })).data

export const wizardPropose = async (
  template: string,
  answers: Record<string, unknown>
): Promise<WizardProposal> =>
  (await axiosInstance.post('/wizard/propose', { template, answers })).data

export const wizardApply = async (
  diffs: WizardDiff[],
  reason: string
): Promise<WizardApplyResponse> =>
  (await axiosInstance.post('/wizard/apply', { diffs, reason })).data

// ── Graph Quality (v-next): dedup · garbage · connectivity · communities ──────

export interface ConnectivityReport {
  total_nodes: number
  total_edges: number
  isolated_nodes: number
  isolated_pct: number
  connected_components: number
  largest_component_size: number
  largest_component_pct: number
  degree: { mean: number; median: number; max: number; degree0: number; degree1: number }
  isolate_sample: string[]
}

export const graphConnectivity = async (): Promise<ConnectivityReport> =>
  (await axiosInstance.get('/graph/connectivity?sample_isolates=8')).data

// Rebuild entity/relation vector indices from the graph (vector-drift recovery)
export const reindexGraphVectors = async (): Promise<{ entities: number; relationships: number; decisions_reprojected: number }> =>
  (await axiosInstance.post('/graph/vectors/reindex?wait=true', undefined, { timeout: 300000 })).data

// Recorded decisions (the (h,r,t,rc) quadruples) — filterable list for the dashboard/decisions view
export const listDecisions = async (
  params: Record<string, string | number> = {}
): Promise<{ decisions: any[]; total_count?: number }> =>
  (await axiosInstance.get('/graph/decisions', { params })).data

// ── Weave (distributed AI dev team) ─────────────────────────────────────────
export type WeaveTask = {
  id: string; title: string; status: string; priority: string; description?: string
  change_request?: string | null; touches: string[]; depends_on: string[]
  assignee?: string | null; created_by?: string | null
  commits: { sha: string; subject: string; touches: string[] }[]
  pull_request?: { branch: string; url: string; title: string; status: string } | null
  reviews: { verdict: string; by: string; notes: string }[]; learnings: string[]
}
export type WeaveWorker = {
  id: string; role: string; host: string; status: string; control: string
  goal?: string; current_task?: string | null; stale?: boolean
}
export type WeaveEnvironment = { id: string; name: string; url: string; status: string }

export const weaveStatus = async (): Promise<any> =>
  (await axiosInstance.get('/weave/status')).data
export const weaveTasks = async (): Promise<{ workspace: string; tasks: WeaveTask[] }> =>
  (await axiosInstance.get('/weave/tasks')).data
export const weaveWorkers = async (): Promise<{ workspace: string; workers: WeaveWorker[] }> =>
  (await axiosInstance.get('/weave/workers')).data
export const weaveEnvironments = async (): Promise<{ workspace: string; environments: WeaveEnvironment[] }> =>
  (await axiosInstance.get('/weave/environments')).data
export const weaveChain = async (taskId: string): Promise<any> =>
  (await axiosInstance.get(`/weave/tasks/${encodeURIComponent(taskId)}/chain`)).data
export const weaveControlWorker = async (id: string, action: 'pause' | 'resume' | 'stop'): Promise<any> =>
  (await axiosInstance.post(`/weave/workers/${encodeURIComponent(id)}/control`, { action })).data
export const weaveAdvanceTask = async (taskId: string, to: string): Promise<any> =>
  (await axiosInstance.post(`/weave/tasks/${encodeURIComponent(taskId)}/advance`, { to })).data
export const weavePromote = async (taskId: string, environment: string): Promise<any> =>
  (await axiosInstance.post(`/weave/tasks/${encodeURIComponent(taskId)}/promote`, { environment })).data
export const weaveReviewAuto = async (taskId: string): Promise<any> =>
  (await axiosInstance.post(`/weave/tasks/${encodeURIComponent(taskId)}/review/auto`)).data

/* ── the project + dev hosts (P8) ──────────────────────────────────────────── */

/** What every developer in this workspace works on. A dev host learns all of
 *  this from its heartbeat, so it is set once here rather than per machine. */
export type WeaveProject = {
  repo: string; base_branch: string; image: string
  test_command: string[]; setup_command: string[]
  description: string; updated_at: number; updated_by: string
}

/** A machine carrying developer containers. `desired_workers` is what the team
 *  asked for; `workers` is what the machine reports actually running. */
export type WeaveHost = {
  id: string; machine: string; status: string; control: string
  seat: string; seat_detail: string
  desired_workers: number; workers: string[]
  repo: string; base_branch: string; image: string
  capabilities: string[]; version: string; stale?: boolean
}

export const weaveProject = async (): Promise<{ workspace: string } & WeaveProject> =>
  (await axiosInstance.get('/weave/project')).data

export const weaveSetProject = async (body: Partial<{
  repo: string; base_branch: string; image: string
  test_command: string[]; setup_command: string[]; description: string
}>): Promise<WeaveProject> =>
  (await axiosInstance.put('/weave/project', body)).data

export const weaveHosts = async (): Promise<{ workspace: string; hosts: WeaveHost[] }> =>
  (await axiosInstance.get('/weave/hosts')).data

export const weaveControlHost = async (
  id: string, action: 'drain' | 'pause' | 'resume' | 'stop'
): Promise<any> =>
  (await axiosInstance.post(`/weave/hosts/${encodeURIComponent(id)}/control`, { action })).data

// ── the senior-developer seat (P5) ───────────────────────────────────────────
//
// `dispatch` starts nothing. It records how many developers each machine should
// run and returns the ordered queue they will claim from; each host reconciles
// on its next heartbeat (A15). `reaches_fleet_via` is in the response because it
// is the thing most likely to be misread as "N workers are now running".

export interface DispatchResult {
  workspace: string
  by: string
  hosts: { act: string; target: string; by: string; detail: Record<string, unknown> }[]
  requested_workers: number
  queue: { id: string; title: string; priority: string; touches: string[] }[]
  reaches_fleet_via: string
  note: string
}

export interface FleetHost extends WeaveHost {
  running: number
  reconciled: boolean
  workers: any[]
}

export const weaveDispatch = async (
  workersPerHost: number,
  hosts?: string[]
): Promise<DispatchResult> =>
  (await axiosInstance.post('/weave/team/dispatch', {
    workers_per_host: workersPerHost,
    ...(hosts ? { hosts } : {})
  })).data

export const weaveFleet = async (): Promise<{ workspace: string; hosts: FleetHost[] }> =>
  (await axiosInstance.get('/weave/team/fleet')).data

export const weaveControlWorkerAction = async (
  id: string,
  action: 'pause' | 'resume' | 'stop' | 'redirect',
  goal = ''
): Promise<any> =>
  (await axiosInstance.post(`/weave/workers/${encodeURIComponent(id)}/control`, {
    action, goal
  })).data

export const weaveScaleHost = async (id: string, desired: number): Promise<any> =>
  (await axiosInstance.post(`/weave/hosts/${encodeURIComponent(id)}/scale`,
    { desired_workers: desired })).data

// Deduplication
export const dedupScan = async (apply: boolean): Promise<any> =>
  (await axiosInstance.post(`/graph/dedup/scan?apply=${apply}`)).data
export const dedupSweep = async (): Promise<any> =>
  (await axiosInstance.post('/graph/dedup/sweep')).data
export const dedupReview = async (): Promise<any> =>
  (await axiosInstance.get('/graph/dedup/review')).data
export const entityMerges = async (): Promise<{ merges: any[] }> =>
  (await axiosInstance.get('/graph/entities/merges')).data
export const entityUnmerge = async (mergeId: string): Promise<any> =>
  (await axiosInstance.post(`/graph/entities/unmerge?merge_id=${encodeURIComponent(mergeId)}`)).data

// Garbage + quarantine
export const garbageScan = async (apply: boolean): Promise<any> =>
  (await axiosInstance.post(`/graph/garbage/scan?apply=${apply}`)).data
export const quarantineList = async (): Promise<{ items: any[]; summary: any }> =>
  (await axiosInstance.get('/graph/quarantine')).data
export const quarantineRestore = async (name: string): Promise<any> =>
  (await axiosInstance.post(`/graph/quarantine/restore?name=${encodeURIComponent(name)}`)).data
export const quarantineDiscard = async (name: string): Promise<any> =>
  (await axiosInstance.post(`/graph/quarantine/discard?name=${encodeURIComponent(name)}`)).data

// Isolate rescue + prune
export const connectivityRescue = async (apply: boolean, limit = 20): Promise<any> =>
  (await axiosInstance.post(`/graph/connectivity/rescue?apply=${apply}&limit=${limit}`)).data
export const pruneIsolates = async (apply: boolean): Promise<any> =>
  (await axiosInstance.post(`/graph/prune/isolates?apply=${apply}`)).data

// Communities
export const communityBuild = async (): Promise<any> =>
  (await axiosInstance.post('/graph/community/build?min_size=3')).data
export const communityList = async (): Promise<{ communities: any[]; summary: any }> =>
  (await axiosInstance.get('/graph/communities')).data
export const communityQuery = async (query: string): Promise<{ response: string; communities: any[] }> =>
  (await axiosInstance.post('/graph/community/query', { query, top_k: 5 })).data

// ── Studio (P3: diff-and-approve authoring) ─────────────────────────────────
export type StudioKind = 'ontology' | 'rule' | 'flow' | 'action' | 'diagram'

export type ArtifactDiff = {
  kind: StudioKind
  artifact_id: string
  to_version: number
  from_version: number | null
  delta: { before: any | null; after: any }
  behaviour_changed: boolean
  origin: string
}

export type StudioSignOff = { approver: string; reason: string; at: string; role: string | null }

export type StudioApplyResult = {
  kind: StudioKind
  artifact_id: string
  version: number
  behaviour_changed: boolean
  sign_off: StudioSignOff
  decision_audit: any | null
}

export type StudioArtifactRow = { kind: StudioKind; artifact_id: string; version: number; revisions: number }

export type StudioVersion = {
  kind: StudioKind
  artifact_id: string
  version: number
  snapshot: any
  from_version: number | null
  behaviour_changed: boolean
  origin: string
  sign_off: StudioSignOff | null
  decision_audit: any | null
}

export type StudioProposeBody = {
  kind: StudioKind
  artifact_id: string
  draft?: any
  spec?: string
  concepts?: Record<string, string[]>
  origin?: string
}

export const studioPropose = async (body: StudioProposeBody): Promise<{ diff: ArtifactDiff }> =>
  (await axiosInstance.post('/studio/propose', body)).data

export const studioAssess = async (diff: ArtifactDiff): Promise<{ diff: ArtifactDiff }> =>
  (await axiosInstance.post('/studio/assess', { diff })).data

// No `approver` parameter, on either of these (A6, D-038). The server derives
// the signer from the token; the client cannot state it, and used to be able to
// — the Studio rendered a text box for it and whatever you typed was recorded as
// the signer. The reason is still yours to give; who you are is not.
export const studioApply = async (
  diff: ArtifactDiff,
  signOff?: { reason?: string }
): Promise<StudioApplyResult> =>
  (await axiosInstance.post('/studio/apply', { diff, ...(signOff || {}) })).data

export const studioRevert = async (
  kind: StudioKind, artifactId: string, toVersion: number, reason: string
): Promise<StudioApplyResult> =>
  (await axiosInstance.post('/studio/revert', {
    kind, artifact_id: artifactId, to_version: toVersion, reason
  })).data

export const studioDraft = async (body: {
  kind: StudioKind; artifact_id: string; instruction: string
  history?: { role: string; content: string }[]
}): Promise<{ reply: string; diff: ArtifactDiff }> =>
  (await axiosInstance.post('/studio/draft', body)).data

export const studioArtifacts = async (): Promise<{ workspace: string; artifacts: StudioArtifactRow[] }> =>
  (await axiosInstance.get('/studio/artifacts')).data

export type StudioGraphNode = { id: string; kind: 'flow' | 'action' | 'rule' | 'object'; label: string }
export type StudioGraphEdge = { src: string; dst: string; rel: string }
export const studioGraph = async (): Promise<{ workspace: string; nodes: StudioGraphNode[]; edges: StudioGraphEdge[] }> =>
  (await axiosInstance.get('/studio/graph')).data

export const studioHistory = async (
  kind: string, artifactId: string
): Promise<{ workspace: string; kind: string; artifact_id: string; history: StudioVersion[] }> =>
  (await axiosInstance.get(`/studio/history/${encodeURIComponent(kind)}/${encodeURIComponent(artifactId)}`)).data

/* ── Diagrams (P6) — shared, server-side project diagrams ─────────────────── */

export type DiagramRow = {
  id: string; title: string; description: string
  type: string; version: number; depicts: string[]; tags: string[]
}
export type DiagramDetail = DiagramRow & { source: string }

export const listDiagrams = async (
  depicts?: string
): Promise<{ workspace: string; diagrams: DiagramRow[] }> =>
  (await axiosInstance.get('/diagrams', { params: depicts ? { depicts } : undefined })).data

export const getDiagram = async (
  id: string, version?: number
): Promise<DiagramDetail> =>
  (await axiosInstance.get(`/diagrams/${encodeURIComponent(id)}`,
    { params: version ? { version } : undefined })).data

export const deleteDiagram = async (id: string): Promise<{ status: string }> =>
  (await axiosInstance.delete(`/diagrams/${encodeURIComponent(id)}`)).data

export const diagramVersions = async (
  id: string
): Promise<{ workspace: string; id: string; history: StudioVersion[] }> =>
  (await axiosInstance.get(`/diagrams/${encodeURIComponent(id)}/versions`)).data

/**
 * Save a diagram to the shared workspace set.
 *
 * The server treats a change to the diagram's *structure* (its nodes and
 * connectors) as governed: without `approver`/`reason` it answers 422. Cosmetic
 * edits — labels, styling, direction, title — need no sign-off. Bad or unsafe
 * mermaid is a 400.
 */
export const saveDiagram = async (body: {
  id: string
  source: string
  title?: string
  description?: string
  depicts?: string[]
  tags?: string[]
  approver?: string
  reason?: string
}): Promise<{ status: string; version: number; id: string }> =>
  (await axiosInstance.post('/diagrams', body)).data

/* ── users (Admin ▸ Users) ──────────────────────────────────────────────────
 *
 * The screens the source never had (R13, D-009). Adding a person used to mean
 * editing an environment variable and restarting the server.
 *
 * No response on this surface carries a password hash — the server has no field
 * for one (R17), and nothing here should ever try to render it.
 */

export type WeaveUser = {
  id: string
  username: string
  role: string
  display_name: string
  email: string
  status: 'active' | 'disabled'
  created_at: string
  updated_at: string
  last_login_at: string
  workspaces: string[]
}

export const listUsers = async (): Promise<WeaveUser[]> => {
  const response = await axiosInstance.get('/users')
  return response.data
}

export const createUser = async (body: {
  username: string
  password: string
  role?: string
  display_name?: string
  email?: string
  workspaces?: string[]
}): Promise<WeaveUser> => {
  const response = await axiosInstance.post('/users', body)
  return response.data
}

export const updateUser = async (
  id: string,
  body: { display_name?: string; email?: string; role?: string; status?: string }
): Promise<WeaveUser> => {
  const response = await axiosInstance.patch(`/users/${id}`, body)
  return response.data
}

export const deleteUser = async (id: string): Promise<void> => {
  await axiosInstance.delete(`/users/${id}`)
}

export const setUserPassword = async (id: string, password: string): Promise<void> => {
  await axiosInstance.post(`/users/${id}/password`, { password })
}

export const setUserWorkspaces = async (
  id: string,
  workspaces: string[]
): Promise<{ workspaces: string[] }> => {
  const response = await axiosInstance.put(`/users/${id}/workspaces`, { workspaces })
  return response.data
}

// ── The four canonical questions (P2's `/ask/*`) ─────────────────────────────
//
// These endpoints have existed since P2 with no UI at all (CR-001 §1). The
// handler behind each is the same one MCP calls — not an equivalent, the same
// one (A9) — so a screen built on these cannot drift from what an agent sees.
//
// Only `why` requires an anchor; the other three answer the whole workspace
// when given none, which is what makes a landing view possible without asking
// the user to pick something first.

export interface AnswerNode {
  id?: string
  type?: string
  /** The node's human-readable name, assembled server-side (U3). */
  label?: string
  locator?: { repo?: string; path?: string; rev?: string; [k: string]: unknown } | null
  locator_error?: string | null
  [key: string]: unknown
}

export interface Answer {
  question: string
  nodes: AnswerNode[]
  count: number
  truncated: boolean
}

export type Question = 'changes' | 'why' | 'features' | 'learnings'

/** The anchor parameter each question takes, and whether it is required. */
export const ASK_ANCHOR: Record<Question, { param: string; required: boolean }> = {
  changes: { param: 'feature', required: false },
  why: { param: 'node', required: true },
  features: { param: 'feature', required: false },
  learnings: { param: 'scope', required: false }
}

export const ask = async (question: Question, anchor?: string): Promise<Answer> => {
  const { param } = ASK_ANCHOR[question]
  const params = anchor ? { [param]: anchor } : undefined
  return (await axiosInstance.get(`/ask/${question}`, { params })).data
}

// ── Projects: where a locator actually points (`/projects/*`) ────────────────
//
// A5 is the reason this surface exists: an artifact references its source by
// `repo · path · rev` and never embeds a copy of it. That only works if `repo`
// resolves to somewhere real, which is what a ProjectLayout registration is —
// the mapping from the name a locator holds to a URL a person can open and a
// checkout an agent can read.
//
// An unregistered repo is not an error, it is the answer: every locator naming
// it will fail to resolve, and the screen should say which repo is missing
// rather than showing a broken link per node.

export interface ProjectLayout {
  name: string
  clone_url: string
  default_rev: string
  description: string
  has_local_checkout: boolean
}

export interface ResolvedLocator {
  repo: string
  path: string
  rev: string
  url: string
  exists: boolean
  anchor?: string | null
  [key: string]: unknown
}

export const listProjects = async (): Promise<{ workspace: string; projects: ProjectLayout[] }> =>
  (await axiosInstance.get('/projects')).data

export const registerProject = async (
  body: { name: string; clone_url?: string; local_path?: string; default_rev?: string; description?: string }
): Promise<ProjectLayout> => (await axiosInstance.post('/projects', body)).data

export const resolveLocator = async (
  repo: string, path: string, rev = '', content = false
): Promise<ResolvedLocator> =>
  (await axiosInstance.get('/projects/resolve', { params: { repo, path, rev, content } })).data
