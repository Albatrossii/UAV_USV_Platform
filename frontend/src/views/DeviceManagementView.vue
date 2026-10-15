<script setup lang="ts">
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { Pencil, Plus, Trash2 } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { createDevice, deleteDevice, updateDevice } from '@/api/device'
import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import { useAuthStore } from '@/stores/auth'
import { useDeviceStore } from '@/stores/device'
import { useMonitoringStore } from '@/stores/monitoring'
import type { Device, DeviceSavePayload, DeviceStatus, DeviceType } from '@/types/device'

const authStore = useAuthStore()
const deviceStore = useDeviceStore()
const monitoringStore = useMonitoringStore()
const formRef = ref<FormInstance>()
const dialogVisible = ref(false)
const deleteDialogVisible = ref(false)
const saving = ref(false)
const deletingId = ref<number | null>(null)
const editingId = ref<number | null>(null)
const deleteTarget = ref<Device | null>(null)
const deleteAcknowledged = ref(false)
const deleteError = ref('')

const form = reactive<DeviceSavePayload>({
  code: '',
  name: '',
  type: 'UAV',
  status: 'UNKNOWN',
  host: '',
  port: null,
  rosNamespace: '',
  description: '',
})

const rules: FormRules<DeviceSavePayload> = {
  code: [{ required: true, message: '请输入设备编号', trigger: 'blur' }],
  name: [{ required: true, message: '请输入设备名称', trigger: 'blur' }],
  type: [{ required: true, message: '请选择设备类型', trigger: 'change' }],
  status: [{ required: true, message: '请选择运行状态', trigger: 'change' }],
  port: [
    {
      validator: (_rule, value, callback) => {
        if (value === null || value === undefined || value === '') return callback()
        if (Number(value) >= 1 && Number(value) <= 65535) return callback()
        callback(new Error('端口范围应为 1-65535'))
      },
      trigger: 'blur',
    },
  ],
}

const typeOptions: Array<{ label: string; value: DeviceType }> = [
  { label: '无人机 UAV', value: 'UAV' },
  { label: '无人艇 USV', value: 'USV' },
  { label: '灯塔目标', value: 'LIGHTHOUSE' },
  { label: 'ROS 节点', value: 'ROS_NODE' },
  { label: 'Unity 节点', value: 'UNITY_NODE' },
]

const statusOptions: Array<{ label: string; value: DeviceStatus }> = [
  { label: '在线', value: 'ONLINE' },
  { label: '离线', value: 'OFFLINE' },
  { label: '维护中', value: 'MAINTENANCE' },
  { label: '未知', value: 'UNKNOWN' },
]

const canManage = computed(() => authStore.user?.role === 'ADMIN')
const dialogTitle = computed(() => (editingId.value ? '编辑设备' : '新增设备'))
const deleteTitle = computed(() => (deleteTarget.value ? `删除 ${deleteTarget.value.name}` : '删除设备'))
const runtimeNodeByCode = computed(() => {
  const map = new Map<string, DeviceStatus>()
  monitoringStore.nodes.forEach((node) => map.set(node.code.toLowerCase(), node.status))
  return map
})
const onlineCount = computed(() => deviceStore.records.filter((device) => displayStatus(device) === 'ONLINE').length)
const bridgeNodeCount = computed(() => deviceStore.records.filter((device) => ['ROS_NODE', 'UNITY_NODE'].includes(device.type)).length)
const attentionCount = computed(() => deviceStore.records.filter((device) => displayStatus(device) !== 'ONLINE').length)

function typeLabel(type: DeviceType) {
  return typeOptions.find((item) => item.value === type)?.label ?? type
}

function statusLabel(status: DeviceStatus) {
  return statusOptions.find((item) => item.value === status)?.label ?? status
}

function displayStatus(row: Device | Record<string, unknown>) {
  const device = row as Device
  return runtimeNodeByCode.value.get(device.code.toLowerCase()) ?? device.status
}

function statusTag(status: DeviceStatus) {
  if (status === 'ONLINE') return 'success'
  if (status === 'MAINTENANCE') return 'warning'
  if (status === 'OFFLINE') return 'danger'
  return 'info'
}

function statusClass(status: DeviceStatus) {
  return status.toLowerCase()
}

function typeClass(type: DeviceType) {
  return type.toLowerCase().replace('_', '-')
}

function deviceInitial(type: DeviceType) {
  if (type === 'UAV') return 'UAV'
  if (type === 'USV') return 'USV'
  if (type === 'LIGHTHOUSE') return '灯塔'
  if (type === 'ROS_NODE') return 'ROS'
  if (type === 'UNITY_NODE') return '3D'
  return 'OBJ'
}

function endpoint(row: Device | Record<string, unknown>) {
  if (!row.host && !row.port) return '--'
  return `${row.host ?? ''}${row.port ? `:${row.port}` : ''}`
}

function formatTime(value: string) {
  if (!value) return '--'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function resetForm() {
  editingId.value = null
  Object.assign(form, {
    code: '',
    name: '',
    type: 'UAV',
    status: 'UNKNOWN',
    host: '',
    port: null,
    rosNamespace: '',
    description: '',
  })
  formRef.value?.clearValidate()
}

function openCreate() {
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: Device | Record<string, unknown>) {
  const device = row as Device
  editingId.value = device.id
  Object.assign(form, {
    code: device.code,
    name: device.name,
    type: device.type,
    status: device.status,
    host: device.host ?? '',
    port: device.port,
    rosNamespace: device.rosNamespace ?? '',
    description: device.description ?? '',
  })
  formRef.value?.clearValidate()
  dialogVisible.value = true
}

async function load(page = 0) {
  deviceStore.keyword = ''
  deviceStore.type = undefined
  deviceStore.status = undefined
  monitoringStore.type = undefined
  monitoringStore.status = undefined
  await Promise.all([
    deviceStore.refresh({ page }),
    monitoringStore.refresh({}, true),
  ])
}

async function submit() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return

  saving.value = true
  try {
    const payload = { ...form, port: form.port ? Number(form.port) : null }
    if (editingId.value) {
      await updateDevice(editingId.value, payload)
      ElMessage.success('设备已更新')
    } else {
      await createDevice(payload)
      ElMessage.success('设备已创建')
    }
    dialogVisible.value = false
    await load(deviceStore.page)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

function openDelete(row: Device | Record<string, unknown>) {
  deleteTarget.value = row as Device
  deleteAcknowledged.value = false
  deleteError.value = ''
  deleteDialogVisible.value = true
}

function closeDeleteDialog() {
  if (deletingId.value !== null) return
  deleteDialogVisible.value = false
  deleteTarget.value = null
  deleteAcknowledged.value = false
  deleteError.value = ''
}

async function confirmDelete() {
  if (!deleteTarget.value || !deleteAcknowledged.value) return
  const device = deleteTarget.value
  deleteError.value = ''

  try {
    deletingId.value = device.id
    await deleteDevice(device.id)
    ElMessage.success('设备已删除')
    deleteDialogVisible.value = false
    deleteTarget.value = null
    deleteAcknowledged.value = false
    deleteError.value = ''
    await load(Math.max(0, deviceStore.records.length === 1 ? deviceStore.page - 1 : deviceStore.page))
  } catch (error) {
    deleteError.value = error instanceof Error ? error.message : '删除失败'
    ElMessage.error(deleteError.value)
  } finally {
    deletingId.value = null
  }
}

onMounted(() => {
  monitoringStore.connectEvents()
  void load(0)
})

onBeforeUnmount(() => monitoringStore.disconnectEvents())
</script>

<template>
  <ConsoleLayout title="设备管理" eyebrow="DEVICE REGISTRY" class="device-management-theme">
    <template #actions>
      <el-button v-if="canManage" type="primary" :icon="Plus" @click="openCreate">新增设备</el-button>
    </template>

    <el-alert
      v-if="deviceStore.error"
      title="设备数据加载失败"
      :description="deviceStore.error"
      type="error"
      show-icon
      :closable="false"
      class="section-alert"
    />

    <section class="page-metric-grid">
      <article class="console-stat-card">
        <span>设备总数</span>
        <strong>{{ deviceStore.total }}</strong>
        <small>平台登记设备</small>
      </article>
      <article class="console-stat-card">
        <span>在线设备</span>
        <strong>{{ onlineCount }}</strong>
        <small>实时状态优先</small>
      </article>
      <article class="console-stat-card warning">
        <span>待检查</span>
        <strong>{{ attentionCount }}</strong>
        <small>离线、维护或未知</small>
      </article>
      <article class="console-stat-card">
        <span>桥接节点</span>
        <strong>{{ bridgeNodeCount }}</strong>
        <small>ROS / Unity</small>
      </article>
    </section>

    <section class="console-panel table-panel">
      <div class="panel-heading">
        <div>
          <h2>设备列表</h2>
          <p>统一维护真实/登记设备状态；Unity 虚拟场景设备不计入在线数量。</p>
        </div>
        <el-tag effect="plain">共 {{ deviceStore.total }} 个</el-tag>
      </div>

      <el-table v-loading="deviceStore.loading" :data="deviceStore.records" class="console-table">
        <el-table-column label="设备编号" min-width="210">
          <template #default="{ row }">
            <div class="asset-name-cell">
              <span class="asset-mini-mark" :class="typeClass(row.type)">{{ deviceInitial(row.type) }}</span>
              <div>
                <strong>{{ row.name }}</strong>
                <small>{{ row.code }}</small>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="类型" min-width="130">
          <template #default="{ row }">{{ typeLabel(row.type) }}</template>
        </el-table-column>
        <el-table-column label="状态" min-width="120">
          <template #default="{ row }">
            <el-tag class="device-table-status" :type="statusTag(displayStatus(row))" effect="plain">
              {{ statusLabel(displayStatus(row)) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="通信地址" min-width="190">
          <template #default="{ row }">{{ endpoint(row) }}</template>
        </el-table-column>
        <el-table-column label="ROS 命名空间" min-width="160">
          <template #default="{ row }">{{ row.rosNamespace || '--' }}</template>
        </el-table-column>
        <el-table-column label="更新时间" min-width="130">
          <template #default="{ row }">{{ formatTime(row.updatedAt) }}</template>
        </el-table-column>
        <el-table-column v-if="canManage" label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" :icon="Pencil" @click="openEdit(row)">编辑</el-button>
            <el-button
              link
              type="danger"
              :icon="Trash2"
              :loading="deletingId === row.id"
              :disabled="deletingId !== null"
              @click="openDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="table-footer">
        <el-pagination
          popper-class="device-management-popper"
          background
          layout="total, prev, pager, next, sizes"
          :total="deviceStore.total"
          :current-page="deviceStore.page + 1"
          :page-size="deviceStore.size"
          :page-sizes="[5, 10, 20, 50]"
          @current-change="(page: number) => load(page - 1)"
          @size-change="(size: number) => { deviceStore.size = size; load(0) }"
        />
      </div>
    </section>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="620px" class="device-dialog" @closed="resetForm">
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <div class="form-grid">
          <el-form-item label="设备编号" prop="code"><el-input v-model="form.code" placeholder="例如 uav-01" /></el-form-item>
          <el-form-item label="设备名称" prop="name"><el-input v-model="form.name" placeholder="例如 协同无人机" /></el-form-item>
          <el-form-item label="设备类型" prop="type">
            <el-select v-model="form.type" popper-class="device-management-popper"><el-option v-for="item in typeOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select>
          </el-form-item>
          <el-form-item label="运行状态" prop="status">
            <el-select v-model="form.status" popper-class="device-management-popper"><el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select>
          </el-form-item>
          <el-form-item label="主机地址" prop="host"><el-input v-model="form.host" placeholder="例如 172.30.244.87" /></el-form-item>
          <el-form-item label="端口" prop="port"><el-input-number v-model="form.port" :min="1" :max="65535" controls-position="right" class="full-control" /></el-form-item>
        </div>
        <el-form-item label="ROS 命名空间" prop="rosNamespace"><el-input v-model="form.rosNamespace" placeholder="例如 /uav_usv/uav" /></el-form-item>
        <el-form-item label="说明" prop="description"><el-input v-model="form.description" type="textarea" :rows="3" placeholder="记录节点职责、部署位置或联调用途" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="deleteDialogVisible"
      :title="deleteTitle"
      width="520px"
      class="device-delete-dialog"
      :close-on-click-modal="deletingId === null"
      @closed="closeDeleteDialog"
    >
      <div v-if="deleteTarget" class="delete-confirm">
        <el-alert v-if="deleteError" title="删除失败" :description="deleteError" type="error" show-icon :closable="false" />
        <el-alert
          title="此操作会将设备从平台列表中删除"
          description="删除不会停止 ROS、Unity 或 WSL 中正在运行的进程。如需停进程，请到运行监控中执行停止。"
          type="warning"
          show-icon
          :closable="false"
        />
        <dl class="delete-device-meta">
          <div><dt>设备名称</dt><dd>{{ deleteTarget.name }}</dd></div>
          <div><dt>设备编号</dt><dd>{{ deleteTarget.code }}</dd></div>
          <div><dt>设备类型</dt><dd>{{ typeLabel(deleteTarget.type) }}</dd></div>
          <div><dt>当前状态</dt><dd>{{ statusLabel(displayStatus(deleteTarget)) }}</dd></div>
        </dl>
        <el-checkbox v-model="deleteAcknowledged">我确认删除该设备，并了解这不会停止 ROS 或 Unity 进程</el-checkbox>
      </div>
      <template #footer>
        <el-button :disabled="deletingId !== null" @click="closeDeleteDialog">取消</el-button>
        <el-button
          type="danger"
          :icon="Trash2"
          :loading="deletingId !== null"
          :disabled="!deleteAcknowledged || deletingId !== null"
          @click="confirmDelete"
        >
          确认删除
        </el-button>
      </template>
    </el-dialog>
  </ConsoleLayout>
</template>
