const ZH = [
  '删除', '移除', '注销', '解绑', '退款', '退订', '退货', '支付', '付款', '充值',
  '购买', '下单', '提交订单', '立即购买', '确认提交', '确认删除', '确认支付',
  '确认下单', '清空', '解散', '停用', '禁用',
];

const EN = [
  'delete', 'remove', 'cancel subscription', 'unsubscribe', 'pay now', 'purchase',
  'place order', 'buy now', 'checkout', 'confirm delete', 'confirm payment',
  'confirm submit', 'deactivate', 'disable account', 'wipe',
];

export function checkSensitiveText(text: string | null | undefined): { safe: boolean; matched?: string } {
  if (!text) return { safe: true };
  const lower = text.toLowerCase();
  for (const w of ZH) {
    if (text.includes(w)) return { safe: false, matched: w };
  }
  for (const w of EN) {
    if (lower.includes(w)) return { safe: false, matched: w };
  }
  return { safe: true };
}
