/* @vitest-environment jsdom */

import { screen } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { NotFoundPage } from './NotFoundPage'
import { renderWithProviders } from '../test/renderWithProviders'

describe('NotFoundPage', () => {
  it('renders headline and back to chat link', () => {
    renderWithProviders(
      <Routes>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>,
      { initialRoute: '/unknown-path' },
    )

    expect(screen.getByRole('heading', { name: /page not found/i })).toBeTruthy()
    expect(screen.getByRole('link', { name: /back to chat/i }).getAttribute('href')).toBe('/')
  })
})
