import { describe, expect, it } from 'vitest';
import { addM, dimText, fmtShort, monthRange, pctTxt, usd } from './format';

describe('format', () => {
  it('adds months across year ends', () => {
    expect(addM('2026-01', -1)).toBe('2025-12');
    expect(addM('2025-12', 1)).toBe('2026-01');
    expect(monthRange('2025-11', '2026-02')).toEqual(['2025-11', '2025-12', '2026-01', '2026-02']);
  });
  it('formats compact money', () => {
    expect(usd(1_240_000)).toBe('$1.24M');
    expect(usd(8300)).toBe('$8.3k');
    expect(usd(-640)).toBe('−$640');
    expect(usd(null)).toBe('—');
  });
  it('calls tiny changes flat', () => {
    expect(pctTxt(0.002)).toBe('flat');
    expect(pctTxt(-0.12)).toBe('▼ 12%');
  });
  it('hides sort prefixes on cyclic dimensions', () => {
    expect(dimText('01 Jan', 'cyclic')).toBe('Jan');
    expect(dimText('2025', 'time')).toBe('2025');
  });
  it('shortens axis labels', () => {
    expect(fmtShort(12500, 'money')).toBe('$13k');
  });
});
