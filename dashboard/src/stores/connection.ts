import { defineStore } from 'pinia'

export const useConnectionStore = defineStore('connection', {
  state: () => ({
    isConnected: false,
    ws: null as WebSocket | null,
    reconnectAttempts: 0,
    reconnectDelay: 5000, // 5 seconds
    adminWsUrl: `ws://${window.location.hostname}:8000/ws/admin`,
    reconnectTimer: null as number | null, // Store timer ID
  }),

  actions: {
    connect() {
      console.log(`Attempting to connect to ${this.adminWsUrl} (attempt ${this.reconnectAttempts + 1})`)

      // Clear any existing reconnect timer
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer)
        this.reconnectTimer = null
      }

      // Close existing connection if present
      if (this.ws) {
        console.log('Closing existing WebSocket connection')
        this.ws.close()
      }

      // Create WebSocket connection
      this.ws = new WebSocket(this.adminWsUrl)

      // Set up connection timeout to ensure it doesn't hang indefinitely
      const connectionTimeout = setTimeout(() => {
        if (this.ws && this.ws.readyState === WebSocket.CONNECTING) {
          console.log('Connection attempt timed out')
          this.ws.close() // Close the hanging connection
          this.scheduleReconnect() // Schedule a new connection attempt
        }
      }, this.reconnectDelay) // Use the same delay as reconnection delay

      this.ws.onopen = () => {
        clearTimeout(connectionTimeout) // Clear timeout on successful connection
        console.log('Connected to Vedal Studio admin WebSocket')
        this.isConnected = true
        this.reconnectAttempts = 0 // Reset attempts on successful connection

        // Clear reconnect timer on successful connection
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer)
          this.reconnectTimer = null
        }

        console.log('Connection established successfully, no reconnection needed')
      }

      this.ws.onclose = (event) => {
        clearTimeout(connectionTimeout) // Clear timeout on close
        console.log('Disconnected from Vedal Studio admin WebSocket:', event.code, event.reason)
        this.isConnected = false

        // Schedule reconnection
        this.scheduleReconnect()
      }

      this.ws.onerror = (error) => {
        clearTimeout(connectionTimeout) // Clear timeout on error
        console.error('WebSocket error:', error)
        this.isConnected = false

        // Schedule reconnection on error
        this.scheduleReconnect()
      }
    },

    scheduleReconnect() {
      console.log(`Scheduling reconnection in ${this.reconnectDelay}ms (attempt ${this.reconnectAttempts + 1})`)

      // Clear any existing timer
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer)
        this.reconnectTimer = null
      }

      // Schedule reconnection after delay
      this.reconnectTimer = window.setTimeout(() => {
        console.log(`Executing reconnection attempt #${this.reconnectAttempts + 1}`)
        this.reconnectAttempts++
        this.connect() // Try to connect again
      }, this.reconnectDelay)
    },

    disconnect() {
      // Clear any reconnect timer
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer)
        this.reconnectTimer = null
      }

      // Close the WebSocket connection
      if (this.ws) {
        this.ws.close()
        this.ws = null
      }

      this.isConnected = false
      console.log('Disconnected from Vedal Studio admin WebSocket')
    },

    async sendAdminWsMessage(action: string, payload: any = {}): Promise<any> {
      return new Promise((resolve, reject) => {
        if (!this.isConnected || !this.ws) {
          reject(new Error('Not connected to WebSocket'))
          return
        }

        const requestId = `req_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`

        // Create message
        const message = {
          action,
          request_id: requestId,
          payload
        }

        // Set up response handler
        const responseHandler = (event: MessageEvent) => {
          const response = JSON.parse(event.data)
          if (response.request_id === requestId) {
            // Remove this event listener after handling
            this.ws?.removeEventListener('message', responseHandler)

            if (response.type === 'response') {
              resolve(response.payload)
            } else {
              reject(new Error(`Request failed: ${JSON.stringify(response)}`))
            }
          }
        }

        // Add event listener for response
        this.ws.addEventListener('message', responseHandler)

        // Send message
        this.ws.send(JSON.stringify(message))

        // Set timeout to reject if no response
        setTimeout(() => {
          this.ws?.removeEventListener('message', responseHandler)
          reject(new Error('Request timeout'))
        }, 10000) // 10 second timeout
      })
    }
  }
})