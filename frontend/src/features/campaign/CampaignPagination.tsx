const PAGE_GROUP_SIZE = 10;

function formatNumber(value: number): string {
  return new Intl.NumberFormat("ko-KR").format(value);
}

export function CampaignPagination({
  label,
  total,
  page,
  pageSize,
  totalPages,
  summarySuffix,
  onPageChange,
}: {
  label: string;
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  summarySuffix: string;
  onPageChange: (page: number) => void;
}) {
  if (total <= 0) {
    return null;
  }
  const groupStart = Math.floor((page - 1) / PAGE_GROUP_SIZE) * PAGE_GROUP_SIZE + 1;
  const groupEnd = Math.min(groupStart + PAGE_GROUP_SIZE - 1, Math.max(totalPages, 1));
  const pages = Array.from({ length: groupEnd - groupStart + 1 }, (_, index) => groupStart + index);
  const currentStart = (page - 1) * pageSize + 1;
  const currentEnd = Math.min(page * pageSize, total);
  return (
    <div className="campaign-inline-pagination campaign-inline-pagination--grouped">
      <span>{formatNumber(currentStart)}–{formatNumber(currentEnd)} / {formatNumber(total)}{summarySuffix}</span>
      <div className="campaign-inline-pagination__pages">
        <button
          type="button"
          disabled={groupStart === 1}
          onClick={() => onPageChange(groupStart - 1)}
          aria-label={`이전 ${label} 페이지 묶음`}
        >
          ‹
        </button>
        {pages.map((pageNumber) => (
          <button
            className={pageNumber === page ? "campaign-inline-pagination__page campaign-inline-pagination__page--active" : "campaign-inline-pagination__page"}
            type="button"
            key={pageNumber}
            aria-label={`${label} ${pageNumber}페이지`}
            aria-current={pageNumber === page ? "page" : undefined}
            onClick={() => onPageChange(pageNumber)}
          >
            {pageNumber}
          </button>
        ))}
        <button
          type="button"
          disabled={groupEnd >= totalPages}
          onClick={() => onPageChange(groupEnd + 1)}
          aria-label={`다음 ${label} 페이지 묶음`}
        >
          ›
        </button>
      </div>
    </div>
  );
}
