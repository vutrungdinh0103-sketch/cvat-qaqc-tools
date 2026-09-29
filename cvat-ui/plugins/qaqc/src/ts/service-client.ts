// Copyright (C) CVAT QA/QC tools
//
// SPDX-License-Identifier: MIT
//
// Client gọi QA/QC service (FastAPI - xem docs/plugin-install.md, Phase 3).
// Base URL lấy theo thứ tự: localStorage['qaqc.serviceUrl'] -> env QAQC_SERVICE_URL -> '/qaqc'.
//
// Lưu ý: service chưa được cài đặt trong repo này; plugin hiển thị thông báo lỗi
// thân thiện nếu chưa gọi được. Khi service sẵn sàng, chỉ cần deploy đúng URL.

export interface QAQCIssue {
    rule_id: string;
    severity: 'error' | 'warning' | 'info';
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
    issues: QAQCIssue[];
    warnings: string[];
}

export interface QAQCRequestOptions {
    rules?: string;
    only?: string;
    refresh?: boolean;
}

const DEFAULT_BASE_URL = process.env.QAQC_SERVICE_URL || '/qaqc';

export function serviceBaseUrl(): string {
    try {
        return window.localStorage.getItem('qaqc.serviceUrl') || DEFAULT_BASE_URL;
    } catch (error) {
        return DEFAULT_BASE_URL;
    }
}

function buildQuery(options: QAQCRequestOptions): string {
    const params = new URLSearchParams();
    if (options.rules) params.set('rules', options.rules);
    if (options.only) params.set('only', options.only);
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
