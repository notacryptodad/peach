/* 口味看板：偏好维度摘要，以及按这些维度从公开目录取回的作品。 */
import { useState } from 'react';
import { RiCompassLine } from '@remixicon/react';
import { useMutation, useQuery } from '@tanstack/react-query';

import { Button, ButtonLink } from '@/components/base/buttons/button';
import { Select, SelectItem } from '@/components/base/select/select';
import { Input } from '@/components/base/input/input';

import { errorMessage } from '../../api';
import { cardClass } from '../components/card';
import { EmptyState } from '../components/empty-state';
import { Note } from '../components/note';
import { queryClient } from '../query';
import {
  activateViewer, createViewer, fetchTasteBoard, fetchViewers, TASTE_BOARD_KEY, VIEWERS_KEY,
  type TasteBoard as TasteBoardData, type TasteBoardWork,
} from './taste';

const CARD = `${cardClass()} flex flex-col gap-4`;

function WorkCard({ work }: { work: TasteBoardWork }) {
  const meta = [work.code, work.band, work.studio, work.release_date].filter(Boolean).join(' · ');
  return (
    <article className="min-w-0 rounded-xl bg-background-primary-default p-3">
      <a href={work.href} className="flex gap-3 outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring">
        {work.cover_url
          ? <img src={work.cover_url} alt="" width={64} height={96} className="h-24 w-16 shrink-0 rounded-md object-cover" />
          : <span aria-hidden className="inline-grid h-24 w-16 shrink-0 place-items-center rounded-md bg-background-tertiary-default px-1 text-center text-caption-1-medium text-text-secondary">{work.code}</span>}
        <span className="flex min-w-0 flex-col gap-1">
          <span className="truncate text-body-medium text-text-primary">{work.title}</span>
          <span className="truncate text-caption-1-regular text-text-secondary">{meta}</span>
          {work.performers ? <span className="truncate text-body-2-regular text-text-secondary">{work.performers}</span> : null}
          {work.reasons?.[0] ? <span className="line-clamp-2 text-body-2-regular text-text-secondary">{work.reasons[0]}</span> : null}
        </span>
      </a>
    </article>
  );
}

async function refreshViewerTaste() {
  await queryClient.invalidateQueries({ queryKey: VIEWERS_KEY });
  await queryClient.invalidateQueries({ queryKey: ['taste'] });
}

function Viewers() {
  const [name, setName] = useState('');
  const list = useQuery({
    queryKey: VIEWERS_KEY, retry: false, refetchOnWindowFocus: false,
    queryFn: ({ signal }) => fetchViewers(signal),
  });
  const activate = useMutation({ mutationFn: activateViewer, onSuccess: refreshViewerTaste });
  const create = useMutation({
    mutationFn: () => createViewer(name.trim()),
    onSuccess: async () => { setName(''); await refreshViewerTaste(); },
  });
  const viewers = list.data?.viewers ?? [];
  const active = viewers.find((viewer) => viewer.active)?.id;
  const message = activate.error || create.error;
  if (!viewers.length && !message) return null;
  return (
    <div className="flex flex-col gap-3">
      {viewers.length ? (
        <Select aria-label="使用者" selectedKey={active}
          onSelectionChange={(key) => { if (key != null && String(key) !== active) activate.mutate(String(key)); }}>
          {viewers.map((viewer) => <SelectItem key={viewer.id} id={viewer.id}>{viewer.name}</SelectItem>)}
        </Select>
      ) : null}
      <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
        event.preventDefault();
        if (name.trim()) create.mutate();
      }}>
        <Input aria-label="新使用者名称" placeholder="新使用者名称" maxLength={40} value={name}
          onChange={setName} className="min-w-0 grow" />
        <Button type="submit" size="small" disabled={!name.trim() || create.isPending}>添加使用者</Button>
      </form>
      {message ? <Note tone="error">{errorMessage(message)}</Note> : null}
    </div>
  );
}

function BoardBody({ data }: { data: TasteBoardData }) {
  const dimensions = data.profile?.dimensions || [];
  const works = data.recommendations || [];
  return (
    <>
      <p className="text-caption-1-regular text-text-secondary">
        {`${data.source?.name || '在线目录'} · 匿名读取`}
      </p>
      {dimensions.length ? (
        <ul aria-label="口味摘要" className="flex flex-wrap gap-2">
          {dimensions.flatMap((dimension) => dimension.items.slice(0, 3).map((item) => (
            <li key={`${dimension.kind}:${item.name}`}
              className="rounded-full bg-background-tertiary-default px-2.5 py-1 text-caption-1-medium text-text-primary">
              {`${dimension.label} · ${item.name}`}
            </li>
          )))}
        </ul>
      ) : null}
      {data.status === 'thin' && data.onboarding ? (
        <EmptyState shell="plain" icon={RiCompassLine} title={data.onboarding.title} actions={
          <>
            {data.onboarding.steps.filter((step) => step.href).map((step) => (
              <ButtonLink key={step.href} href={step.href} size="small"
                variant={step.secondary ? 'secondary' : 'primary'}>{step.title}</ButtonLink>
            ))}
          </>
        }>{data.onboarding.detail}</EmptyState>
      ) : null}
      {data.status === 'unavailable' ? <Note tone="warning">{data.message || '在线目录暂时没有返回作品。'}</Note> : null}
      {data.status === 'empty' ? (
        <EmptyState shell="plain" icon={RiCompassLine} title="没有对上口味的在线作品">
          {data.message || '这次检索没有对上口味的作品。'}
        </EmptyState>
      ) : null}
      {works.length ? (
        <div className="inline-grid w-full gap-3 sm:grid-cols-2">
          {works.map((work) => <WorkCard key={work.code} work={work} />)}
        </div>
      ) : null}
    </>
  );
}

export function TasteBoard() {
  const board = useQuery({
    queryKey: TASTE_BOARD_KEY, retry: false, refetchOnWindowFocus: false,
    queryFn: ({ signal }) => fetchTasteBoard(signal),
  });
  return (
    <section aria-label="口味看板" className={CARD}>
      <h2 className="text-title-2-medium text-text-primary">口味看板</h2>
      <Viewers />
      {board.isPending ? (
        <div data-skeleton="" aria-busy="true" role="status" aria-label="正在读取口味看板"
          className="h-40 rounded-xl skeleton-sheen" />
      ) : null}
      {board.isError ? (
        <Note tone="error" title="口味看板读取失败">{errorMessage(board.error)}</Note>
      ) : null}
      {board.data ? <BoardBody data={board.data} /> : null}
    </section>
  );
}
