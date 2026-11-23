'use strict';
'require view';
'require rpc';
'require ui';
'require poll';
'require fs';

var status = rpc.declare({ object: 'rax.firmware', method: 'status' });
var progress = rpc.declare({ object: 'rax.firmware', method: 'progress' });
var write = rpc.declare({ object: 'rax.firmware', method: 'write', params: ['slot', 'force'] });
var select = rpc.declare({ object: 'rax.firmware', method: 'select', params: ['slot', 'force'] });
var configStatus = rpc.declare({ object: 'rax.config', method: 'status' });
var backup = rpc.declare({ object: 'rax.config', method: 'backup' });
var checkBackup = rpc.declare({ object: 'rax.config', method: 'check' });
var restore = rpc.declare({ object: 'rax.config', method: 'restore' });
var reset = rpc.declare({ object: 'rax.config', method: 'reset' });

function checked(result) {
    if (result.code !== 0) throw new Error(result.output || '操作失败');
    return result;
}

function reboot(address) {
    return fs.exec('/sbin/reboot', []).then(checked).then(function() {
        ui.showModal('正在重启', [E('p', {'class':'spinning'}, '请等待设备重新连接。恢复配置改变了 LAN 地址时，请使用恢复后的地址访问。')]);
        ui.awaitReconnect(address);
    });
}

function settingRow(title, control, description) {
    return E('div', {'class':'cbi-value'}, [
        E('div', {'class':'cbi-value-title'}, title),
        E('div', {'class':'cbi-value-field'}, [control,
            description ? E('div', {'class':'cbi-value-description'}, description) : ''])]);
}

return view.extend({
    load: function() { return Promise.all([status(), configStatus()]); },
    render: function(data) {
        var state = data[0], pending = data[1].pending;
        var readonly = !L.hasViewPermission();
        var target = E('select', { 'class': 'cbi-input-select' }, [E('option', {value:'a'}, 'A'), E('option', {value:'b'}, 'B')]);
        target.value = state.running === 'a' ? 'b' : 'a';
        var bootTarget = E('select', {'class':'cbi-input-select'}, [E('option', {value:'a'}, 'A'), E('option', {value:'b'}, 'B')]);
        bootTarget.value = state.running === 'a' ? 'b' : 'a';
        var force = E('input', {type:'checkbox'});
        var bootForce = E('input', {type:'checkbox'});
        var log = E('pre', {'style':'display:none;white-space:pre-wrap'}, '');
        var uploadButton, switchButton;
        var notifyError = function(error) { ui.addNotification(null, E('p', {}, error.message || String(error)), 'error'); };
        var configButtons = [];
        var configBusy = function(value) {
            configButtons.forEach(function(button) { button.disabled = value || readonly || pending; });
            uploadButton.disabled = value || readonly || pending;
            switchButton.disabled = value || readonly || pending;
            [target, bootTarget, force, bootForce].forEach(function(input) {
                input.disabled = value || readonly || pending;
            });
        };
        var confirmConfig = function(title, description, operation, address, files) {
            ui.showModal(title, [E('p', {}, description), files ? E('pre', {}, files) : '',
                E('div', {'class':'right'}, [
                    E('button', {'class':'cbi-button', click:ui.hideModal}, '取消'), ' ',
                    E('button', {'class':'cbi-button cbi-button-negative', click:ui.createHandlerFn(null, function() {
                        configBusy(true);
                        return operation().then(checked).then(function() {
                            pending = true;
                            return reboot(address);
                        }).catch(function(e) { ui.hideModal(); notifyError(e); }).finally(function() { configBusy(false); });
                    })}, '继续并重启')])]);
        };
        var backupButton = E('button', {'class':'cbi-button cbi-button-action', click:ui.createHandlerFn(null, function(ev) {
            var button = ev.currentTarget;
            return backup().then(checked).then(function() {
                var form = E('form', {method:'post', action:L.env.cgi_base + '/cgi-download'}, [
                    E('input', {type:'hidden', name:'sessionid', value:rpc.getSessionID()}),
                    E('input', {type:'hidden', name:'path', value:'/tmp/rax-backup.tar.gz'}),
                    E('input', {type:'hidden', name:'filename', value:'backup-rax3000h-' + new Date().toISOString().slice(0,10) + '.tar.gz'})]);
                button.parentNode.appendChild(form);
                form.submit(); form.remove();
            }).catch(notifyError);
        })}, '生成备份');
        var restoreButton = E('button', {'class':'cbi-button cbi-button-action', click:ui.createHandlerFn(null, function(ev) {
            return ui.uploadFile('/tmp/rax-config-upload.tar.gz', ev.currentTarget).then(function() {
                return checkBackup();
            }).then(checked).then(function(result) {
                confirmConfig('恢复配置', '恢复备份并重启会中断联网，管理员密码、SSH 密钥和网络设置将使用备份中的值。',
                    restore, window.location.host, result.output);
            }).catch(notifyError);
        })}, '上传备份');
        var resetButton = E('button', {'class':'cbi-button cbi-button-negative', click:function() {
            confirmConfig('恢复默认设置', '将清除 ImmortalWrt 用户配置、管理员密码、SSH 密钥和服务设置并重启，联网将中断。默认 LAN 地址为 192.168.77.1，双频无线关闭，管理员密码为空。设备驱动、校准数据、A/B 系统及启动槽选择保留。',
                reset, '192.168.77.1');
        }}, '恢复默认设置');
        configButtons = [backupButton, restoreButton, resetButton];
        uploadButton = E('button', {'class':'cbi-button cbi-button-action', click: function() {
            if (target.value === state.running) { notifyError(new Error('不能覆盖当前运行槽。')); return; }
            ui.uploadFile('/tmp/rax-upload.ubi', uploadButton).then(function() {
                ui.showModal('刷写 rootfs', [E('p', {}, '将替换 '+target.value.toUpperCase()+' 槽 rootfs，保留内核。刷写后可单独选择启动槽。'),
                    E('div', {'class':'right'}, [
                        E('button', {'class':'cbi-button', click:ui.hideModal}, '取消'), ' ',
                        E('button', {'class':'cbi-button cbi-button-action', click:function() {
                            ui.hideModal(); configBusy(true);
                            write(target.value, force.checked).then(function(result) {
                                if (!result.started) throw new Error('未能启动刷写');
                                var timer = function() {
                                    return progress().then(function(p) {
                                        log.style.display='';
                                        log.textContent=p.output || '正在刷写…';
                                        if (!p.busy) {
                                            poll.remove(timer); configBusy(false);
                                            if (p.code !== 0) notifyError(new Error('刷写失败，请查看日志。'));
                                        }
                                    });
                                };
                                poll.add(timer, 1);
                            }).catch(function(e) { configBusy(false); notifyError(e); });
                        }}, '开始刷写')])]);
            }).catch(notifyError);
        }}, '上传 rootfs 并刷写');
        switchButton = E('button', {'class':'cbi-button cbi-button-apply', click:function() {
            ui.showModal('切换启动槽', [E('p', {}, '下次启动使用 '+bootTarget.value.toUpperCase()+' 槽。两个槽共享 ImmortalWrt 配置和设备资产。'),
                E('div', {'class':'right'}, [E('button', {'class':'cbi-button',click:ui.hideModal}, '取消'), ' ',
                    E('button', {'class':'cbi-button cbi-button-apply',click:function() {
                        ui.hideModal();
                        configBusy(true);
                        select(bootTarget.value, bootForce.checked).then(checked).then(function() {
                            return reboot(window.location.host);
                        }).catch(function(e) { configBusy(false); notifyError(e); });
                    }}, '切换并重启')])]);
        }}, '切换并重启');
        configBusy(false);
        var slotRows = ['a', 'b'].map(function(slot) {
            var labels = [];
            if (state.running === slot) labels.push('正在运行');
            if (state.selected === slot) labels.push('下次启动');
            return E('tr', {'class':'tr'}, [
                E('td', {'class':'td', 'data-title':'启动槽'}, slot.toUpperCase()),
                E('td', {'class':'td', 'data-title':'当前状态'}, labels.length ? labels.join(' · ') : '未选用')]);
        });
        var page = E('div', {}, [E('h2', {}, '备份、恢复与升级'),
            pending ? E('p', {'class':'alert-message warning'}, ['配置恢复或重置已准备完成，重启后生效。',
                E('button', {'class':'cbi-button', disabled:readonly || null, click:function() {
                    reboot(window.location.host).catch(notifyError);
                }}, '立即重启')]) : '',
            E('div', {}, [
                E('div', {'data-tab':'backup', 'data-tab-title':'备份与恢复'}, [
                    E('div', {'class':'cbi-section'}, [
                        E('h3', {}, '配置备份与恢复'),
                        E('div', {'class':'cbi-section-descr'}, '备份包含网络、无线及其他 UCI 配置、管理员账户、SSH 密钥、定时任务和服务设置。'),
                        settingRow('下载配置备份', backupButton, '生成 tar.gz 配置包。备份含密码和密钥，请妥善保存。'),
                        settingRow('恢复配置备份', restoreButton, '上传已有备份，确认后恢复配置并重启。')]),
                    E('div', {'class':'cbi-section'}, [
                        E('h3', {}, '恢复默认设置'),
                        E('div', {'class':'cbi-section-descr'}, '清除用户设置，恢复当前 ImmortalWrt 的默认配置。'),
                        settingRow('重置配置', resetButton, '此操作会中断联网，设备资产和 A/B 系统保留。')])]),
                E('div', {'data-tab':'firmware', 'data-tab-title':'固件升级'}, [
                    E('div', {'class':'cbi-section'}, [
                        E('h3', {}, '刷写固件'),
                        E('div', {'class':'cbi-section-descr'}, '上传 rootfs UBI 镜像，写入非运行槽。刷写完成后，在「启动槽」中选择系统并重启。'),
                        settingRow('当前运行槽', E('strong', {}, state.running.toUpperCase())),
                        settingRow('写入目标槽', target, '仅替换所选槽的 rootfs，保留内核和配置。'),
                        settingRow('内核兼容性', E('label', {}, [force, ' 强制接受尚未验证的目标内核'])),
                        settingRow('固件镜像', uploadButton, '支持本项目的 rootfs UBI 镜像及同格式的原厂恢复镜像。'),
                        log])]),
                E('div', {'data-tab':'boot', 'data-tab-title':'启动槽'}, [
                    E('div', {'class':'cbi-section'}, [
                        E('h3', {}, 'A/B 槽状态'),
                        E('table', {'class':'table'}, [
                            E('tr', {'class':'tr table-titles'}, [E('th', {'class':'th'}, '启动槽'), E('th', {'class':'th'}, '当前状态')])].concat(slotRows))]),
                    E('div', {'class':'cbi-section'}, [
                        E('h3', {}, '切换启动槽'),
                        E('div', {'class':'cbi-section-descr'}, '两个槽共享 ImmortalWrt 配置和设备资产。切换会立即重启并中断联网。'),
                        settingRow('目标启动槽', bootTarget),
                        settingRow('内核兼容性', E('label', {}, [bootForce, ' 强制接受尚未验证的目标内核'])),
                        settingRow('应用启动选择', switchButton)])])])]);
        ui.tabs.initTabGroup(page.lastElementChild.childNodes);
        return page;
    },
    handleSaveApply:null, handleSave:null, handleReset:null
});
