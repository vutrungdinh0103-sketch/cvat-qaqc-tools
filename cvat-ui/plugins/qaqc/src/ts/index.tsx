// Copyright (C) CVAT QA/QC tools
//
// SPDX-License-Identifier: MIT
//
// Entry point của plugin QA/QC. Webpack nạp file này khi build cvat-ui với
// CLIENT_PLUGINS=qaqc (xem docs/plugin-install.md).
//
// Cơ chế (đã kiểm chứng trên CVAT v2.76):
// 1. cvat-ui dispatch sự kiện `plugins.ready` sau khi tạo window.cvatUI.
// 2. Ta gọi window.cvatUI.registerComponent(builder); builder nhận
//    { dispatch, REGISTER_ACTION, REMOVE_ACTION, actionCreators, core, store }.
// 3. Dùng actionCreators.addUIComponent(path, component, { weight }) để chèn
//    component vào một "điểm mở rộng" (extension point) của UI.
// 4. Builder phải trả về { name, destructor, globalStateDidUpdate? }.
//
// Điểm mở rộng dùng ở đây: `qualityControlPage.tabs.items`. CVAT sẽ GỌI HÀM tab
// với props { key, targetProps } và mong đợi nhận về TabItem { key, label, children }.
//
// Lưu ý (đã kiểm chứng): khi có plugin tab, CVAT sẽ ẩn tab "Requirements" gốc
// (điều kiện render là `!pluginTabs.length && qualitySettings`). Nếu muốn giữ
// tab đó, dùng `actionCreators.updateUIComponent` để override
// `qualityControlPage.task.requirementsTab` và tự render lại.

import React from 'react';

import { ComponentBuilder, PluginEntryPoint } from 'components/plugins-entrypoint';

import { QAQCTab } from './qaqc-tab';

const PLUGIN_NAME = 'QA/QC checker';

const TAB_PATH = 'qualityControlPage.tabs.items';

/**
 * Hàm render tab: CVAT gọi trực tiếp như một hàm (không phải component).
 * Trả về TabItem hoặc null (null = không hiển thị tab).
 */
function renderQAQCTab(props: { key?: number; targetProps?: unknown }): {
    key: string;
    label: string;
    children: React.ReactNode;
} {
    return {
        key: 'qaqc',
        label: 'QA/QC',
        children: <QAQCTab key={props.key} targetProps={props.targetProps as never} />,
    };
}

const builder: ComponentBuilder = ({ actionCreators }) => {
    actionCreators.addUIComponent(TAB_PATH, renderQAQCTab as never, { weight: 100 });

    return {
        name: PLUGIN_NAME,
        destructor: () => {
            // Không cần làm gì thêm: cvat-ui tự gỡ component khi plugin bị destroy.
        },
    };
};

function register(): void {
    if (Object.prototype.hasOwnProperty.call(window, 'cvatUI')) {
        (window as unknown as { cvatUI: { registerComponent: PluginEntryPoint } }).cvatUI
            .registerComponent(builder);
    }
}

window.addEventListener('plugins.ready', register, { once: true });
