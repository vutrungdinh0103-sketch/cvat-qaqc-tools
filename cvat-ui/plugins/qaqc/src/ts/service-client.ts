// Copyright (C) CVAT QA/QC tools
//
// SPDX-License-Identifier: MIT
//
// Client gọi QA/QC service (python -m qaqc serve - xem docs/localhost.md và
// qaqc/service.py). Endpoint của service phải khớp file này: /health,
// /tasks/{id}/report, /tasks/{id}/report.csv, /tasks/{id}/run,
// /tasks/{id}/publish (tạo issue trên CVAT từ lỗi tìm được).
//
// Cách xác định base URL (theo thứ tự):
//   1. localStorage['qaqc.serviceUrl'] - đổi ngay trong browser khi debug:
//        localStorage.setItem('qaqc.serviceUrl', 'http://127.0.0.1:8081')
//   2. Nếu CVAT UI đang mở ở localhost/127.0.0.1 -> mặc định
//      http://127.0.0.1:8081 (QA service chạy trên máy host, service đã bật CORS).
//   3. Mặc định '/qaqc' - cần proxy qua nginx của cvat_ui (xem docs/plugin-install.md).
//
// Lưu ý: KHÔNG dùng `process.env` để cấu hình plugin - cvat-ui dùng `dotenv-webpack`
// và Dockerfile.ui không copy file `.env` vào build context, nên các biến kiểu
// `process.env.QAQC_SERVICE_URL` luôn là `undefined` khi chạy (xem V27).

export interface QAQCIssue {
    rule_id: string;
    severity: 'error' | 'warning' | 'info';
    /** Cấp độ QA: 1 = Overall/Completeness, 2 = Detailed. */
    level: number;
    task_id: number;
    job_id: number | null;
    frame: number;
    object_key: string | null;
    shape_id: number | null;
    track_id: number | null;
    label: string | null;
    message: string;
    fingerprint: string;
    details: Record<string, unknown>;
}

export interface QAQCReport {
    schema_version: string;
    task_id: number;
    task_name: string | null;
    generated_at: string;
    objects_scanned: number;
    frames_scanned: number;
    rules_name: string;
    rules_hash: string;
    counts_by_rule: Record<string, number>;
    counts_by_severity: Record<string, number>;
    /** Số lỗi theo cấp độ QA (chỉ chứa cấp độ thực sự có lỗi). */
    counts_by_level: Record<string, number>;
    issues: QAQCIssue[];
    warnings: string[];
}

export interface QAQCRequestOptions {
    rules?: string;
    only?: string;
    /** Chỉ chạy rule của cấp độ này (1 = Overall, 2 = Detailed). */
    level?: number;
    refresh?: boolean;
}

/**
 * Tham số của `POST /tasks/{id}/publish` (đẩy lỗi QA/QC thành issue trên CVAT).
 * Tên field theo camelCase - được chuyển thành snake_case trong query string.
 */
export interface PublishOptions {
    /** Chỉ đẩy issue có mức độ >= giá trị này (mặc định 'error'). */
    severity?: 'error' | 'warning' | 'info';
    /** Chỉ mô phỏng, không ghi gì lên CVAT. */
    dryRun?: boolean;
    /** Mở lại issue đã resolved nếu lỗi vẫn còn (mặc định true). */
    reopenResolved?: boolean;
    /** Đánh dấu resolved cho issue cũ không còn lỗi. */
    resolveStale?: boolean;
    /** Ghi comment JSON chi tiết kèm mỗi issue (mặc định true). */
    postDetails?: boolean;
    /** Giới hạn số issue tạo mới cho mỗi job (mặc định 50). */
    maxIssuesPerJob?: number;
    /** Giới hạn tổng số issue tạo mới (0 = không giới hạn). */
    maxIssuesTotal?: number;
    /** Chỉ đẩy lỗi của cấp độ này (1 = Overall/Completeness, 2 = Detailed). */
    level?: number;
    /** Bộ rule dùng cho lần chạy (mặc định theo service). */
    rules?: string;
}

/** Một issue vừa được tạo (job + frame + fingerprint để tra ngược lại). */
export interface PublishItem {
    job_id: number;
    frame: number;
    fingerprint: string;
}

/** Kết quả `POST /tasks/{id}/publish` - khớp payload của `qaqc/service.py`. */
export interface PublishResult {
    task_id: number;
    dry_run: boolean;
    min_severity: string;
    issues_total: number;
    counts_by_severity: Record<string, number>;
    created: number;
    reopened: number;
    published: number;
    resolved_stale: number;
    failed: number;
    skipped_existing: number;
    skipped_severity: number;
    skipped_cap: number;
    skipped_resolved: number;
    skipped_unmapped: number;
    items: PublishItem[];
    errors: string[];
    summary: string[];
}

//: Mặc định khi UI mở ở localhost (demo/dev): QA service chạy trên máy host.
const LOCAL_SERVICE_URL = 'http://127.0.0.1:8081';

//: Mặc định khi triển khai thật: đi qua proxy nginx của cvat_ui.
const DEFAULT_BASE_URL = '/qaqc';

export function serviceBaseUrl(): string {
    try {
        const configured = window.localStorage.getItem('qaqc.serviceUrl');
        if (configured) {
            return configured;
        }
    } catch (error) {
        // localStorage có thể bị chặn (chế độ riêng tư) -> bỏ qua, dùng mặc định.
    }

    try {
        if (['localhost', '127.0.0.1'].includes(window.location.hostname)) {
            return LOCAL_SERVICE_URL;
        }
    } catch (error) {
        // window.location không đọc được -> dùng mặc định.
    }

    return DEFAULT_BASE_URL;
}

function buildQuery(options: QAQCRequestOptions): string {
    const params = new URLSearchParams();
    if (options.rules) params.set('rules', options.rules);
    if (options.only) params.set('only', options.only);
    if (options.level) params.set('level', String(options.level));
    if (options.refresh) params.set('refresh', 'true');
    const query = params.toString();
    return query ? `?${query}` : '';
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${serviceBaseUrl()}${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...init,
    });
    if (!response.ok) {
        const text = await response.text();
        throw new Error(`QA/QC service trả về ${response.status}: ${text || response.statusText}`);
    }
    return response.json() as Promise<T>;
}

/**
 * Lấy báo cáo QA/QC của một task từ service.
 */
export async function fetchTaskReport(
    taskId: number,
    options: QAQCRequestOptions = {},
): Promise<QAQCReport> {
    return request<QAQCReport>(`/tasks/${taskId}/report${buildQuery(options)}`);
}

/**
 * Kích hoạt chạy QA/QC cho một task và chờ kết quả (nếu service hỗ trợ chạy nền,
 * endpoint này trả về báo cáo ngay khi hoàn tất).
 */
export async function runTaskQAQC(
    taskId: number,
    options: QAQCRequestOptions = {},
): Promise<QAQCReport> {
    return request<QAQCReport>(`/tasks/${taskId}/run${buildQuery(options)}`, { method: 'POST' });
}

function buildPublishQuery(options: PublishOptions): string {
    const params = new URLSearchParams();
    if (options.severity) params.set('severity', options.severity);
    if (options.dryRun) params.set('dry_run', 'true');
    // Service mặc định reopen_resolved/post_details = true -> chỉ gửi khi cần tắt.
    if (options.reopenResolved === false) params.set('reopen_resolved', 'false');
    if (options.resolveStale) params.set('resolve_stale', 'true');
    if (options.postDetails === false) params.set('post_details', 'false');
    if (typeof options.maxIssuesPerJob === 'number') {
        params.set('max_issues_per_job', String(options.maxIssuesPerJob));
    }
    if (typeof options.maxIssuesTotal === 'number') {
        params.set('max_issues_total', String(options.maxIssuesTotal));
    }
    if (options.level) params.set('level', String(options.level));
    if (options.rules) params.set('rules', options.rules);
    const query = params.toString();
    return query ? `?${query}` : '';
}

/**
 * Đẩy lỗi QA/QC của một task thành *issue* trên CVAT (endpoint chỉ hỗ trợ POST).
 *
 * Service luôn chạy lại QA/QC cho lần đẩy này (đây là thao tác ghi, không dùng
 * cache) nên có thể mất vài giây với task lớn. Việc tạo issue là idempotent theo
 * fingerprint: gọi lại nhiều lần không sinh issue trùng - chỉ báo
 * `skipped_existing`. Nếu service chạy ở chế độ demo (`python -m qaqc serve --demo`)
 * thì service trả lỗi 409 vì không có CVAT để ghi.
 */
export async function publishTaskIssues(
    taskId: number,
    options: PublishOptions = {},
): Promise<PublishResult> {
    return request<PublishResult>(`/tasks/${taskId}/publish${buildPublishQuery(options)}`, {
        method: 'POST',
    });
}
