// Copyright (C) CVAT QA/QC tools
//
// SPDX-License-Identifier: MIT
//
// Tab "QA/QC" hiển thị trong trang Quality control của CVAT.
//
// CVAT render tab plugin bằng cách GỌI HÀM với props `{ key, targetProps }` và
// mong đợi trả về một TabItem ({ key, label, children }) hoặc null
// (xem cvat-ui/src/components/quality-control/quality-control-page.tsx).
// `targetProps` là state của trang QC: { instance, instanceType, ... }.

import React, { useCallback, useEffect, useMemo, useState } from 'react';

import {
    PublishResult,
    QAQCReport,
    fetchTaskReport,
    publishTaskIssues,
    runTaskQAQC,
    serviceBaseUrl,
} from './service-client';

interface TargetProps {
    instance?: { id?: number; instanceType?: string; name?: string } | null;
    instanceType?: string | null;
}

export interface QAQCTabProps {
    key?: number | string;
    targetProps?: TargetProps;
}

const SEVERITY_COLORS: Record<string, string> = {
    error: '#d4380d',
    warning: '#d48806',
    info: '#096dd9',
};

//: Mức độ lỗi tối thiểu sẽ được đẩy thành issue trên CVAT.
type PublishSeverity = 'error' | 'warning' | 'info';

const SEVERITY_RANK: Record<PublishSeverity, number> = { info: 1, warning: 2, error: 3 };

const styles = {
    wrapper: { padding: '8px 0' } as React.CSSProperties,
    toolbar: { display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 } as React.CSSProperties,
    button: {
        padding: '4px 12px',
        border: '1px solid #d9d9d9',
        borderRadius: 4,
        background: '#fff',
        cursor: 'pointer',
    } as React.CSSProperties,
    table: { width: '100%', borderCollapse: 'collapse' as const, fontSize: 13 },
    th: {
        textAlign: 'left' as const,
        borderBottom: '1px solid #f0f0f0',
        padding: '6px 8px',
        whiteSpace: 'nowrap' as const,
    },
    td: { borderBottom: '1px solid #f5f5f5', padding: '6px 8px', verticalAlign: 'top' as const },
    meta: { color: '#8c8c8c', fontSize: 12 },
    error: { color: SEVERITY_COLORS.error },
    primaryButton: {
        padding: '4px 12px',
        border: '1px solid #1677ff',
        borderRadius: 4,
        background: '#1677ff',
        color: '#fff',
        cursor: 'pointer',
    } as React.CSSProperties,
    disabled: { opacity: 0.5, cursor: 'not-allowed' } as React.CSSProperties,
    publishBox: {
        border: '1px solid #f0f0f0',
        borderRadius: 4,
        padding: '8px 12px',
        marginBottom: 12,
        fontSize: 13,
    } as React.CSSProperties,
};

/**
 * Component hiển thị kết quả QA/QC của task đang mở.
 *
 * Ghi chú triển khai: component cố tình dùng HTML thuần (không phụ thuộc antd)
 * để skeleton build được ngay cả khi CVAT đổi version antd. Khi tích hợp thật,
 * có thể thay bằng antd `Table`/`Alert` cho đồng bộ giao diện.
 */
export function QAQCTab(props: QAQCTabProps): JSX.Element {
    const taskId = props.targetProps?.instance?.id;
    const [report, setReport] = useState<QAQCReport | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [severityFilter, setSeverityFilter] = useState<string>('all');
    // 'all' = mọi cấp độ, '1' = Level 1 (Overall/Completeness), '2' = Level 2 (Detailed)
    const [levelFilter, setLevelFilter] = useState<string>('1');
    // Mức độ lỗi tối thiểu sẽ đẩy thành issue ('error' = chỉ đẩy lỗi error).
    const [publishSeverity, setPublishSeverity] = useState<PublishSeverity>('error');
    const [publishing, setPublishing] = useState(false);
    const [publishResult, setPublishResult] = useState<PublishResult | null>(null);
    const [publishError, setPublishError] = useState<string | null>(null);

    const load = useCallback(async (refresh: boolean): Promise<void> => {
        if (typeof taskId !== 'number') {
            return;
        }
        setLoading(true);
        setError(null);
        try {
            const options = levelFilter === 'all' ? {} : { level: Number(levelFilter) };
            const result = refresh
                ? await runTaskQAQC(taskId, options)
                : await fetchTaskReport(taskId, options);
            setReport(result);
        } catch (requestError: unknown) {
            setReport(null);
            setError(requestError instanceof Error ? requestError.message : String(requestError));
        } finally {
            setLoading(false);
        }
    }, [taskId, levelFilter]);

    useEffect(() => {
        void load(false);
    }, [load]);

    const issues = useMemo(() => {
        const all = report?.issues ?? [];
        return severityFilter === 'all' ? all : all.filter((issue) => issue.severity === severityFilter);
    }, [report, severityFilter]);

    const publish = useCallback(async (): Promise<void> => {
        if (typeof taskId !== 'number') {
            return;
        }
        const counts = report?.counts_by_severity ?? {};
        const eligible = Object.entries(counts).reduce(
            (sum, [severity, count]) =>
                SEVERITY_RANK[severity as PublishSeverity] >= SEVERITY_RANK[publishSeverity]
                    ? sum + count
                    : sum,
            0,
        );
        if (!eligible) {
            setPublishResult(null);
            setPublishError(
                `Không có lỗi nào từ mức "${publishSeverity}" trở lên để đẩy. ` +
                    'Hãy bấm "Chạy lại QA/QC" hoặc hạ mức lỗi cần đẩy.',
            );
            return;
        }

        const confirmed = window.confirm(
            `Đẩy ${eligible} lỗi (từ mức "${publishSeverity}") của task #${taskId} thành issue trên CVAT?\n\n` +
                'Issue nằm ở tab Issues của job tương ứng (mở task ở trang Annotate). ' +
                'Bấm lại nút này không sinh issue trùng.',
        );
        if (!confirmed) {
            return;
        }

        setPublishing(true);
        setPublishError(null);
        try {
            const options = {
                severity: publishSeverity,
                ...(levelFilter === 'all' ? {} : { level: Number(levelFilter) }),
            };
            setPublishResult(await publishTaskIssues(taskId, options));
            await load(false);
        } catch (requestError: unknown) {
            setPublishResult(null);
            setPublishError(
                requestError instanceof Error ? requestError.message : String(requestError),
            );
        } finally {
            setPublishing(false);
        }
    }, [taskId, publishSeverity, levelFilter, report, load]);

    if (typeof taskId !== 'number') {
        return (
            <div style={styles.wrapper}>
                Chưa xác định được task của tab QA/QC (props thiếu
                {' '}
                <code>instance.id</code>
                ).
            </div>
        );
    }

    return (
        <div style={styles.wrapper}>
            <div style={styles.toolbar}>
                <button
                    type="button"
                    style={styles.button}
                    disabled={loading}
                    onClick={() => void load(true)}
                >
                    {loading ? 'Đang kiểm tra...' : 'Chạy lại QA/QC'}
                </button>
                <button
                    type="button"
                    style={styles.button}
                    disabled={loading}
                    onClick={() => void load(false)}
                >
                    Tải lại kết quả
                </button>
                <select
                    value={levelFilter}
                    onChange={(event) => setLevelFilter(event.target.value)}
                    style={{ padding: '4px 8px' }}
                >
                    <option value="1">Level 1 – Overall/Completeness</option>
                    <option value="2">Level 2 – Chi tiết</option>
                    <option value="all">Tất cả cấp độ</option>
                </select>
                <select
                    value={severityFilter}
                    onChange={(event) => setSeverityFilter(event.target.value)}
                    style={{ padding: '4px 8px' }}
                >
                    <option value="all">Tất cả mức độ</option>
                    <option value="error">Chỉ error</option>
                    <option value="warning">Chỉ warning</option>
                    <option value="info">Chỉ info</option>
                </select>
                <select
                    value={publishSeverity}
                    onChange={(event) => setPublishSeverity(event.target.value as PublishSeverity)}
                    style={{ padding: '4px 8px' }}
                    title="Mức độ lỗi tối thiểu sẽ đẩy thành issue trên CVAT"
                >
                    <option value="error">Đẩy issue: chỉ error</option>
                    <option value="warning">Đẩy issue: warning + error</option>
                    <option value="info">Đẩy issue: tất cả mức độ</option>
                </select>
                <button
                    type="button"
                    style={{ ...styles.primaryButton, ...(publishing ? styles.disabled : {}) }}
                    disabled={publishing || loading || !report}
                    onClick={() => void publish()}
                >
                    {publishing ? 'Đang đẩy issue...' : 'Đẩy issue lên CVAT'}
                </button>
                <span style={styles.meta}>
                    Service:
                    {' '}
                    {serviceBaseUrl()}
                </span>
            </div>

            {publishError && (
                <div style={{ ...styles.error, marginBottom: 12 }}>
                    Không đẩy được issue lên CVAT: {publishError}
                </div>
            )}

            {publishResult && (
                <div style={styles.publishBox}>
                    <strong>
                        {publishResult.dry_run ? 'Mô phỏng (chưa ghi lên CVAT): ' : 'Đã đẩy issue: '}
                    </strong>
                    {`tạo mới ${publishResult.created}, mở lại ${publishResult.reopened}, `}
                    {`đã tồn tại ${publishResult.skipped_existing}, lỗi ${publishResult.failed} `}
                    {`(tổng ${publishResult.issues_total} lỗi, mức >= ${publishResult.min_severity})`}
                    <div style={styles.meta}>
                        Xem issue trong CVAT: mở task ở trang <strong>Annotate</strong> → panel phải →
                        tab <strong>Issues</strong>. Mỗi issue gắn job + frame nên bấm vào là nhảy tới frame lỗi.
                    </div>
                    {!!publishResult.errors.length && (
                        <ul style={{ ...styles.meta, marginBottom: 0 }}>
                            {publishResult.errors.map((item) => (
                                <li key={item}>{item}</li>
                            ))}
                        </ul>
                    )}
                </div>
            )}

            {error && (
                <div style={{ ...styles.error, marginBottom: 12 }}>
                    Không lấy được kết quả QA/QC:
                    {' '}
                    {error}
                    <div style={styles.meta}>
                        Kiểm tra QA service đã chạy và biến QAQC_SERVICE_URL
                        (hoặc localStorage[&apos;qaqc.serviceUrl&apos;]) đã đúng chưa.
                    </div>
                </div>
            )}

            {report && (
                <>
                    <div style={{ marginBottom: 8 }}>
                        <strong>{report.issues.length}</strong>
                        {' lỗi / '}
                        {report.frames_scanned}
                        {' frame có annotation (đã quét '}
                        {report.objects_scanned}
                        {' object)'}
                        {!!report.counts_by_level && (
                            <span style={styles.meta}>
                                {' — Level 1: '}
                                {report.counts_by_level['1'] ?? 0}
                                {', Level 2: '}
                                {report.counts_by_level['2'] ?? 0}
                                {` (bộ rule: ${report.rules_name})`}
                            </span>
                        )}
                    </div>

                    <table style={styles.table}>
                        <thead>
                            <tr>
                                <th style={styles.th}>Mức độ</th>
                                <th style={styles.th}>Cấp độ</th>
                                <th style={styles.th}>Rule</th>
                                <th style={styles.th}>Frame</th>
                                <th style={styles.th}>Nhãn</th>
                                <th style={styles.th}>Mô tả</th>
                            </tr>
                        </thead>
                        <tbody>
                            {issues.map((issue) => (
                                <tr key={`${issue.rule_id}-${issue.fingerprint}`}>
                                    <td style={{ ...styles.td, color: SEVERITY_COLORS[issue.severity] }}>
                                        {issue.severity}
                                    </td>
                                    <td style={styles.td}>{`L${issue.level}`}</td>
                                    <td style={styles.td}>{issue.rule_id}</td>
                                    <td style={styles.td}>{issue.frame}</td>
                                    <td style={styles.td}>{issue.label ?? '-'}</td>
                                    <td style={styles.td}>{issue.message}</td>
                                </tr>
                            ))}
                            {!issues.length && (
                                <tr>
                                    <td style={styles.td} colSpan={6}>
                                        Không có lỗi nào ở cấp độ / mức độ đang lọc.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>

                    {!!report.warnings.length && (
                        <ul style={{ ...styles.meta, marginTop: 12 }}>
                            {report.warnings.map((warning) => (
                                <li key={warning}>{warning}</li>
                            ))}
                        </ul>
                    )}
                </>
            )}
        </div>
    );
}

export default React.memo(QAQCTab);

