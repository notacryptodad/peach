/* 外部链接：当前使用者的页面清单。地址和元数据记在馆藏里，媒体文件留在原地址。 */
import { useState } from 'react';
import { RiExternalLinkLine, RiLink, RiSearchLine } from '@remixicon/react';
import { useMutation, useQuery } from '@tanstack/react-query';

import { confirmModal } from '@peach/legacy/ui';

import { Button, ButtonLink } from '@/components/base/buttons/button';
import { Input } from '@/components/base/input/input';

import { errorMessage } from '../../api';

import { cardClass } from '../components/card';
import { EmptyState } from '../components/empty-state';
import { Note } from '../components/note';
import { queryClient } from '../query';
import { busyProps } from '../settings/use-action';
import {
  EXTERNAL_LINKS_KEY, fetchExternalLinks, linkMatches, saveExternalLink, searchExternalLinks,
  type ExternalLink, type ExternalLinkHit,
} from './taste';

const CARD = `${cardClass()} flex flex-col gap-4`;

function refreshLinks() {
  return queryClient.invalidateQueries({ queryKey: ['taste'] });
}

function LinkRow({ item, toast }: { item: ExternalLink; toast: (message: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState(item.description);
  const [site, setSite] = useState(item.site);
  const [tag, setTag] = useState('');
  const star = useMutation({
    mutationFn: () => saveExternalLink({ action: 'star', id: item.id, liked: !item.liked }),
    onSuccess: async () => {
      await refreshLinks();
      toast(item.liked ? '已取消标星' : '已标星');
    },
  });
  const save = useMutation({
    mutationFn: () => saveExternalLink({
      action: 'update', id: item.id, title: title.trim(), description: description.trim(), site: site.trim(),
    }),
    onSuccess: async () => {
      setEditing(false);
      await refreshLinks();
      toast('已保存链接资料');
    },
  });
  const tagWrite = useMutation({
    mutationFn: (body: { tag: string; operation: 'add' | 'remove' }) =>
      saveExternalLink({ action: 'tag', id: item.id, ...body }),
    onSuccess: async (_result, body) => {
      if (body.operation === 'add') setTag('');
      await refreshLinks();
      toast(body.operation === 'add' ? '已添加标签' : '已去掉标签');
    },
  });
  const remove = useMutation({
    mutationFn: () => saveExternalLink({ action: 'remove', id: item.id }),
    onSuccess: async () => {
      await refreshLinks();
      toast('已移除外部链接');
    },
  });
  const meta = [item.site, item.code, item.studio, item.release_date].filter(Boolean).join(' · ');
  const message = star.error || save.error || tagWrite.error || remove.error;
  return (
    <article className="flex flex-col gap-3 rounded-xl bg-background-primary-default p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-body-medium text-text-primary">{item.title}</h3>
          {meta ? <p className="truncate text-caption-1-regular text-text-secondary">{meta}</p> : null}
          <p className="truncate text-caption-1-regular text-text-secondary" title={item.url}>{item.url}</p>
          {item.description ? <p className="line-clamp-2 text-body-2-regular text-text-secondary">{item.description}</p> : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <ButtonLink href={item.href} target="_blank" rel="noopener noreferrer" variant="secondary" size="small"
            trailingIcon={RiExternalLinkLine}>打开</ButtonLink>
          <Button size="small" variant="secondary" aria-pressed={item.liked} {...busyProps(star.isPending)}
            onClick={() => star.mutate()}>{item.liked ? '已标星' : '标星'}</Button>
          <Button size="small" variant="secondary" onClick={() => setEditing((open) => !open)}>编辑</Button>
          <Button size="small" variant="danger" {...busyProps(remove.isPending)} onClick={() => {
            void confirmModal({
              title: '移除外部链接',
              body: `从当前清单移除「${item.title}」。`,
              confirmLabel: '移除外部链接',
              danger: true,
              onConfirm: () => remove.mutateAsync(),
            });
          }}>移除</Button>
        </div>
      </div>
      {item.tags.length ? (
        <ul aria-label={`${item.title}的标签`} className="flex flex-wrap gap-2">
          {item.tags.map((label) => (
            <li key={label}>
              <Button size="small" variant="secondary" aria-label={`去掉标签 ${label}`}
                onClick={() => tagWrite.mutate({ tag: label, operation: 'remove' })}>{label}</Button>
            </li>
          ))}
        </ul>
      ) : null}
      <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
        event.preventDefault();
        if (tag.trim() && !tagWrite.isPending) tagWrite.mutate({ tag: tag.trim(), operation: 'add' });
      }}>
        <Input aria-label={`给${item.title}添加标签`} placeholder="标签" maxLength={80} value={tag}
          onChange={setTag} className="min-w-0 grow" />
        <Button type="submit" size="small" variant="secondary" disabled={!tag.trim()}
          {...busyProps(tagWrite.isPending)}>添加标签</Button>
      </form>
      {editing ? (
        <form className="flex flex-col gap-2" onSubmit={(event) => {
          event.preventDefault();
          if (title.trim()) save.mutate();
        }}>
          <Input aria-label="标题" placeholder="标题" maxLength={200} value={title} onChange={setTitle} />
          <Input aria-label="说明" placeholder="说明" maxLength={500} value={description} onChange={setDescription} />
          <Input aria-label="站点" placeholder="站点" maxLength={80} value={site} onChange={setSite} />
          <Button type="submit" size="small" variant="secondary" disabled={!title.trim()} {...busyProps(save.isPending)}>保存</Button>
        </form>
      ) : null}
      {message ? <Note tone="error">{errorMessage(message)}</Note> : null}
    </article>
  );
}

export function ExternalLinks({ toast }: { toast: (message: string) => void }) {
  const [draft, setDraft] = useState('');
  const [filter, setFilter] = useState('');
  const [hits, setHits] = useState<ExternalLinkHit[] | null>(null);
  const list = useQuery({
    queryKey: EXTERNAL_LINKS_KEY, retry: false, refetchOnWindowFocus: false,
    queryFn: ({ signal }) => fetchExternalLinks(signal),
  });
  const add = useMutation({
    mutationFn: (body: Record<string, unknown>) => saveExternalLink({ action: 'add', ...body }),
    onSuccess: async () => {
      setDraft('');
      setHits(null);
      await refreshLinks();
      toast('已添加外部链接');
    },
  });
  const search = useMutation({
    mutationFn: (query: string) => searchExternalLinks(query),
    onSuccess: (result) => setHits(result.hits),
  });
  const items = (list.data?.items ?? []).filter((item) => linkMatches(item, filter));
  const urlLike = /^https?:\/\//i.test(draft.trim());
  const message = add.error || search.error || list.error;
  const submit = () => {
    const text = draft.trim();
    if (!text || add.isPending || search.isPending) return;
    if (urlLike) add.mutate({ url: text });
    else search.mutate(text);
  };
  return (
    <section aria-label="外部链接" className={CARD}>
      <h2 className="text-title-2-medium text-text-primary">外部链接</h2>
      <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); submit(); }}>
        <Input aria-label="页面地址或检索词" placeholder="页面地址或检索词" maxLength={2000} value={draft}
          onChange={setDraft} autoComplete="off" className="min-w-0 grow" />
        <Button type="submit" size="small" variant="secondary" disabled={!draft.trim() && !add.isPending}
          {...busyProps(add.isPending || search.isPending)}>{urlLike || !draft.trim() ? '添加' : '检索'}</Button>
      </form>
      {hits ? (
        hits.length ? (
          <ul aria-label="目录结果" className="flex flex-col gap-2">
            {hits.map((hit) => (
              <li key={hit.url} className="flex flex-wrap items-center justify-between gap-2">
                <span className="min-w-0 truncate text-body-2-regular text-text-primary">
                  {[hit.code, hit.title].filter(Boolean).join(' · ')}
                </span>
                <Button size="small" variant="secondary" onClick={() => add.mutate({ origin: 'catalog', ...hit })}>
                  收录
                </Button>
              </li>
            ))}
          </ul>
        ) : <EmptyState shell="plain" icon={RiSearchLine} title="没有找到作品">换一个番号或页面地址。</EmptyState>
      ) : null}
      {list.data?.items.length || filter.trim() ? (
        <Input aria-label="筛选外部链接" placeholder="筛选外部链接" leadingIcon={RiSearchLine} value={filter}
          onChange={setFilter} />
      ) : null}
      {list.isPending ? (
        <div data-skeleton="" aria-busy="true" role="status" aria-label="正在读取外部链接"
          className="h-24 rounded-xl skeleton-sheen" />
      ) : null}
      {message ? <Note tone="error">{errorMessage(message)}</Note> : null}
      {list.data && !items.length ? (
        <EmptyState shell="plain" icon={filter.trim() ? RiSearchLine : RiLink}
          title={filter.trim() ? '没有匹配的外部链接' : '还没有外部链接'}>
          {filter.trim() ? '换一个标题、地址或标签。' : '粘贴页面地址，或用番号检索公开目录。'}
        </EmptyState>
      ) : null}
      {items.map((item) => <LinkRow key={item.id} item={item} toast={toast} />)}
    </section>
  );
}
