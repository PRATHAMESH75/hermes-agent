/**
 * #147808: a gateway group created from the dialog or the group view's Start
 * button appears in the roster list without a manual refresh. The list reads
 * `groups.list` once per route, so a create has to tell it to read again.
 *
 * Real SDK components and stores; only the gateway transport is mocked.
 */

import type * as HermesSdk from '@hermes/plugin-sdk'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { CanonicalGroupList } from './canonical-group-registry'
import { CreateGroupChatDialog } from './create-dialog'
import type * as GroupChatParts from './group-chat-parts'
import { GroupChatWorkspace } from './group-chat-view'
import { translateBots } from './i18n-test-helper'
import type { RosterRow } from './types'

const { request } = vi.hoisted(() => ({ request: vi.fn() }))

vi.mock('@hermes/plugin-sdk', async importOriginal => {
  const sdk = await importOriginal<typeof HermesSdk>()
  const { en } = await import('@/i18n/en')

  return {
    ...sdk,
    host: {
      ...sdk.host,
      requestProfile: request,
      notify: vi.fn(),
      openWorkspace: vi.fn(() => () => undefined),
      request: (method: string, params?: Record<string, unknown>) => request(null, method, params),
      connections: vi.fn(async () => []),
      state: {
        ...sdk.host.state,
        connectionId: sdk.atom<string | null>('local'),
        profile: sdk.atom('default'),
        gateway: sdk.atom('open')
      }
    },
    useI18n: () => ({ locale: 'en', t: en }),
    usePluginI18n: () => translateBots
  }
})
vi.mock('./group-chat-parts', async importOriginal => ({
  ...await importOriginal<typeof GroupChatParts>(),
  GroupImageControls: () => null
}))

const roster: RosterRow[] = [{ name: 'alpha', connectionId: 'local' }, { name: 'beta', connectionId: 'local' }]
const existing = { room_id: 'room-1', name: 'Launch crew', members: [] }

let rooms: { room_id: string; name: string; members: unknown[] }[] = []
let refuseCreate = false

const listReads = () => request.mock.calls.filter(call => call[1] === 'groups.list').length

beforeEach(() => {
  rooms = [existing]
  refuseCreate = false
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
  request.mockReset().mockImplementation(async (_route, method, params) => {
    if (method === 'groups.capabilities') {return { driver: true, persistent_process: true }}

    if (method === 'groups.list') {return { rooms, next_offset: null }}

    if (method === 'groups.create') {
      if (refuseCreate) {throw new Error('driver unavailable')}
      const room = { room_id: params.room_id, name: params.name, members: params.members }
      rooms = [...rooms, room]

      return { room }
    }

    return {}
  })
})

afterEach(cleanup)

async function submitDialog() {
  render(<CreateGroupChatDialog onClose={vi.fn()} onCreated={vi.fn()} open roster={roster} />)

  for (const checkbox of screen.getAllByRole('checkbox').slice(0, 3)) {fireEvent.click(checkbox)}
  await waitFor(() => expect(request.mock.calls.map(call => call[1])).toContain('groups.capabilities'))
  await act(async () => {})
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Create Group (2)' })) })
}

it('a room created from the dialog reaches the mounted list', async () => {
  render(<CanonicalGroupList onOpen={vi.fn()} />)
  await screen.findByRole('button', { name: 'Launch crew' })

  await submitDialog()

  expect(rooms).toHaveLength(2)
  // `hidden`: the still-open modal marks the list behind it aria-hidden.
  expect(await screen.findByRole('button', { hidden: true, name: rooms[1].name })).toBeTruthy()
  expect(screen.getByRole('button', { hidden: true, name: 'Launch crew' })).toBeTruthy()
})

it("a room started from the group view's button reaches the mounted list", async () => {
  render(<CanonicalGroupList onOpen={vi.fn()} />)
  await screen.findByRole('button', { name: 'Launch crew' })
  await act(async () => { render(<GroupChatWorkspace group="Night shift" members={roster} />) })

  await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Start gateway group' })) })

  expect(await screen.findByRole('button', { name: 'Night shift' })).toBeTruthy()
})

it('a refused create leaves the list alone', async () => {
  render(<CanonicalGroupList onOpen={vi.fn()} />)
  await screen.findByRole('button', { name: 'Launch crew' })
  refuseCreate = true
  const reads = listReads()

  await submitDialog()

  expect(request.mock.calls.map(call => call[1])).toContain('groups.create')
  expect(listReads()).toBe(reads)
})
