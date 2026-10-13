import { render } from '@testing-library/react'
import { App } from '@/app/App'

describe('App routing', () => {
  it('mounts app shell', () => {
    const { container } = render(<App />)
    expect(container).toBeTruthy()
  })
})
