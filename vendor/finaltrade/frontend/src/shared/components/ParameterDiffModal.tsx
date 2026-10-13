import { useMemo, useState } from 'react'
import { App as AntdApp, Checkbox, Modal, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { applyAIParameterProposal } from '@/shared/api/endpoints'
import { useAIParameterApplyStore } from '@/state/aiParameterApplyStore'
import type { AIParameterProposalChange, AIParameterProposalResponse } from '@/types/contracts'

type ParameterDiffModalProps = {
  proposal: AIParameterProposalResponse | null
  open: boolean
  onClose: () => void
}

export function ParameterDiffModal({ proposal, open, onClose }: ParameterDiffModalProps) {
  const { message } = AntdApp.useApp()
  const notifyApplied = useAIParameterApplyStore((state) => state.notifyApplied)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [confirmHighRisk, setConfirmHighRisk] = useState(false)
  const [applying, setApplying] = useState(false)

  const changes = proposal?.changes ?? []
  const hasHighRisk = changes.some((item) => item.risk_level === 'high' && selectedIds.includes(item.change_id))

  const columns = useMemo<ColumnsType<AIParameterProposalChange>>(
    () => [
      {
        title: '字段',
        dataIndex: 'target',
        width: 180,
      },
      {
        title: '原值',
        dataIndex: 'old_value',
        render: (value) => (value === undefined || value === null ? '—' : String(value)),
      },
      {
        title: '新值',
        dataIndex: 'new_value',
        render: (value) => (value === undefined || value === null ? '—' : String(value)),
      },
      {
        title: '风险',
        dataIndex: 'risk_level',
        width: 80,
        render: (value: string) => (
          <Tag color={value === 'high' ? 'red' : value === 'medium' ? 'orange' : 'green'}>
            {value || 'low'}
          </Tag>
        ),
      },
      {
        title: '说明',
        dataIndex: 'reason',
        ellipsis: true,
      },
    ],
    [],
  )

  async function handleApply() {
    if (!proposal || selectedIds.length === 0) return
    setApplying(true)
    try {
      const result = await applyAIParameterProposal(proposal.proposal_id, selectedIds, confirmHighRisk)
      if (result.applied.length === 0) {
        message.warning('没有变更被应用，请检查校验错误或风险确认。')
        return
      }
      notifyApplied(result.applied)
      message.success(`已应用 ${result.applied.length} 项参数变更`)
      onClose()
    } catch {
      message.error('应用参数失败')
    } finally {
      setApplying(false)
    }
  }

  return (
    <Modal
      title="AI 参数建议预览"
      open={open}
      onCancel={onClose}
      width={760}
      okText="确认应用"
      cancelText="取消"
      okButtonProps={{ disabled: selectedIds.length === 0, loading: applying }}
      onOk={() => void handleApply()}
      afterOpenChange={(visible) => {
        if (visible && proposal) {
          const validIds = proposal.changes
            .filter((item) => !item.validation_error)
            .map((item) => item.change_id)
          setSelectedIds(validIds)
          setConfirmHighRisk(false)
        }
      }}
    >
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {proposal?.summary}
        </Typography.Paragraph>
        <Table
          rowKey="change_id"
          size="small"
          pagination={false}
          columns={columns}
          dataSource={changes}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys.map(String)),
            getCheckboxProps: (record) => ({
              disabled: Boolean(record.validation_error),
            }),
          }}
        />
        {changes.some((item) => item.validation_error) ? (
          <Typography.Text type="danger">
            部分建议未通过校验，已禁用勾选。
          </Typography.Text>
        ) : null}
        {hasHighRisk ? (
          <Checkbox checked={confirmHighRisk} onChange={(event) => setConfirmHighRisk(event.target.checked)}>
            我确认应用高风险参数变更
          </Checkbox>
        ) : null}
      </Space>
    </Modal>
  )
}
