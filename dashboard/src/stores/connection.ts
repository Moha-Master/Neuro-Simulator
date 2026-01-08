import { defineStore } from 'pinia'

export const useConnectionStore = defineStore('connection', {
  state: () => ({
    // Vedal Studio connection
    isConnected: false,
    vedalWs: null as WebSocket | null,
    vedalReconnectAttempts: 0,
    vedalReconnectDelay: 5000, // 5 seconds
    vedalWsUrl: `ws://${window.location.hostname}:8000/ws/admin`,
    vedalReconnectTimer: null as number | null, // Store timer ID

    // Neuro Sama connection
    isNeuroSamaConnected: false,
    neuroSamaWs: null as WebSocket | null,
    neuroSamaReconnectAttempts: 0,
    neuroSamaReconnectDelay: 5000, // 5 seconds
    neuroSamaWsUrl: '',
    neuroSamaReconnectTimer: null as number | null, // Store timer ID

    // Configuration
    config: null as any,
  }),

  actions: {
    // Connect to Vedal Studio first
    connectToVedal() {
      console.log(`Attempting to connect to Vedal Studio at ${this.vedalWsUrl} (attempt ${this.vedalReconnectAttempts + 1})`)

      // Clear any existing reconnect timer
      if (this.vedalReconnectTimer) {
        clearTimeout(this.vedalReconnectTimer)
        this.vedalReconnectTimer = null
      }

      // Close existing connection if present
      if (this.vedalWs) {
        console.log('Closing existing Vedal Studio WebSocket connection')
        this.vedalWs.close()
      }

      // Create WebSocket connection to Vedal Studio
      this.vedalWs = new WebSocket(this.vedalWsUrl)

      // Set up connection timeout to ensure it doesn't hang indefinitely
      const connectionTimeout = setTimeout(() => {
        if (this.vedalWs && this.vedalWs.readyState === WebSocket.CONNECTING) {
          console.log('Vedal Studio connection attempt timed out')
          this.vedalWs.close() // Close the hanging connection
          this.scheduleVedalReconnect() // Schedule a new connection attempt
        }
      }, this.vedalReconnectDelay) // Use the same delay as reconnection delay

      this.vedalWs.onopen = () => {
        clearTimeout(connectionTimeout) // Clear timeout on successful connection
        console.log('Connected to Vedal Studio admin WebSocket')
        this.isConnected = true
        this.vedalReconnectAttempts = 0 // Reset attempts on successful connection

        // Clear reconnect timer on successful connection
        if (this.vedalReconnectTimer) {
          clearTimeout(this.vedalReconnectTimer)
          this.vedalReconnectTimer = null
        }

        console.log('Vedal Studio connection established successfully')

        // Now get the configuration to connect to other modules
        this.getConfig();
      }

      this.vedalWs.onclose = (event) => {
        clearTimeout(connectionTimeout) // Clear timeout on close
        console.log('Disconnected from Vedal Studio admin WebSocket:', event.code, event.reason)
        this.isConnected = false
        this.isNeuroSamaConnected = false // Also disconnect Neuro Sama when Vedal Studio disconnects

        // Schedule reconnection
        this.scheduleVedalReconnect()
      }

      this.vedalWs.onerror = (error) => {
        clearTimeout(connectionTimeout) // Clear timeout on error
        console.error('Vedal Studio WebSocket error:', error)
        this.isConnected = false
        this.isNeuroSamaConnected = false // Also disconnect Neuro Sama when Vedal Studio has error

        // Schedule reconnection on error
        this.scheduleVedalReconnect()
      }
    },

    // Get configuration from Vedal Studio and connect to other modules
    async getConfig() {
      try {
        const configResponse = await this.sendVedalWsMessage('get_config', {})
        if (configResponse.status === 'success') {
          this.config = configResponse.config

          // Extract Neuro Sama server settings
          const neuroSamaConfig = this.config?.neuro_sama?.server_settings
          if (neuroSamaConfig) {
            const host = neuroSamaConfig.host || '127.0.0.1'
            const port = neuroSamaConfig.port || 8001
            this.neuroSamaWsUrl = `ws://${host}:${port}/ws/admin`

            // Now connect to Neuro Sama
            this.connectToNeuroSama()
          } else {
            console.error('Neuro Sama server settings not found in config')
          }
        } else {
          console.error('Failed to get config from Vedal Studio:', configResponse.message)
        }
      } catch (error) {
        console.error('Error getting config from Vedal Studio:', error)
      }
    },

    // Connect to Neuro Sama
    connectToNeuroSama() {
      console.log(`Attempting to connect to Neuro Sama at ${this.neuroSamaWsUrl} (attempt ${this.neuroSamaReconnectAttempts + 1})`)

      // Clear any existing reconnect timer
      if (this.neuroSamaReconnectTimer) {
        clearTimeout(this.neuroSamaReconnectTimer)
        this.neuroSamaReconnectTimer = null
      }

      // Close existing connection if present
      if (this.neuroSamaWs) {
        console.log('Closing existing Neuro Sama WebSocket connection')
        this.neuroSamaWs.close()
      }

      // Create WebSocket connection to Neuro Sama
      this.neuroSamaWs = new WebSocket(this.neuroSamaWsUrl)

      // Set up connection timeout to ensure it doesn't hang indefinitely
      const connectionTimeout = setTimeout(() => {
        if (this.neuroSamaWs && this.neuroSamaWs.readyState === WebSocket.CONNECTING) {
          console.log('Neuro Sama connection attempt timed out')
          this.neuroSamaWs.close() // Close the hanging connection
          this.scheduleNeuroSamaReconnect() // Schedule a new connection attempt
        }
      }, this.neuroSamaReconnectDelay) // Use the same delay as reconnection delay

      this.neuroSamaWs.onopen = () => {
        clearTimeout(connectionTimeout) // Clear timeout on successful connection
        console.log('Connected to Neuro Sama admin WebSocket')
        this.isNeuroSamaConnected = true
        this.neuroSamaReconnectAttempts = 0 // Reset attempts on successful connection

        // Clear reconnect timer on successful connection
        if (this.neuroSamaReconnectTimer) {
          clearTimeout(this.neuroSamaReconnectTimer)
          this.neuroSamaReconnectTimer = null
        }

        console.log('Neuro Sama connection established successfully')
      }

      this.neuroSamaWs.onclose = (event) => {
        clearTimeout(connectionTimeout) // Clear timeout on close
        console.log('Disconnected from Neuro Sama admin WebSocket:', event.code, event.reason)
        this.isNeuroSamaConnected = false

        // Schedule reconnection
        this.scheduleNeuroSamaReconnect()
      }

      this.neuroSamaWs.onerror = (error) => {
        clearTimeout(connectionTimeout) // Clear timeout on error
        console.error('Neuro Sama WebSocket error:', error)
        this.isNeuroSamaConnected = false

        // Schedule reconnection on error
        this.scheduleNeuroSamaReconnect()
      }
    },

    scheduleVedalReconnect() {
      console.log(`Scheduling Vedal Studio reconnection in ${this.vedalReconnectDelay}ms (attempt ${this.vedalReconnectAttempts + 1})`)

      // Clear any existing timer
      if (this.vedalReconnectTimer) {
        clearTimeout(this.vedalReconnectTimer)
        this.vedalReconnectTimer = null
      }

      // Schedule reconnection after delay
      this.vedalReconnectTimer = window.setTimeout(() => {
        console.log(`Executing Vedal Studio reconnection attempt #${this.vedalReconnectAttempts + 1}`)
        this.vedalReconnectAttempts++
        this.connectToVedal() // Try to connect again
      }, this.vedalReconnectDelay)
    },

    scheduleNeuroSamaReconnect() {
      console.log(`Scheduling Neuro Sama reconnection in ${this.neuroSamaReconnectDelay}ms (attempt ${this.neuroSamaReconnectAttempts + 1})`)

      // Clear any existing timer
      if (this.neuroSamaReconnectTimer) {
        clearTimeout(this.neuroSamaReconnectTimer)
        this.neuroSamaReconnectTimer = null
      }

      // Schedule reconnection after delay
      this.neuroSamaReconnectTimer = window.setTimeout(() => {
        console.log(`Executing Neuro Sama reconnection attempt #${this.neuroSamaReconnectAttempts + 1}`)
        this.neuroSamaReconnectAttempts++
        this.connectToNeuroSama() // Try to connect again
      }, this.neuroSamaReconnectDelay)
    },

    disconnect() {
      // Clear any reconnect timers
      if (this.vedalReconnectTimer) {
        clearTimeout(this.vedalReconnectTimer)
        this.vedalReconnectTimer = null
      }

      if (this.neuroSamaReconnectTimer) {
        clearTimeout(this.neuroSamaReconnectTimer)
        this.neuroSamaReconnectTimer = null
      }

      // Close the WebSocket connections
      if (this.vedalWs) {
        this.vedalWs.close()
        this.vedalWs = null
      }

      if (this.neuroSamaWs) {
        this.neuroSamaWs.close()
        this.neuroSamaWs = null
      }

      this.isConnected = false
      this.isNeuroSamaConnected = false
      console.log('Disconnected from all WebSocket connections')
    },

    async sendVedalWsMessage(action: string, payload: any = {}): Promise<any> {
      return new Promise((resolve, reject) => {
        if (!this.isConnected || !this.vedalWs) {
          reject(new Error('Not connected to Vedal Studio WebSocket'))
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
            this.vedalWs?.removeEventListener('message', responseHandler)

            if (response.type === 'response') {
              resolve(response.payload)
            } else {
              reject(new Error(`Request failed: ${JSON.stringify(response)}`))
            }
          }
        }

        // Add event listener for response
        this.vedalWs.addEventListener('message', responseHandler)

        // Send message
        this.vedalWs.send(JSON.stringify(message))

        // Set timeout to reject if no response
        setTimeout(() => {
          this.vedalWs?.removeEventListener('message', responseHandler)
          reject(new Error('Request timeout'))
        }, 10000) // 10 second timeout
      })
    },

    async sendNeuroSamaWsMessage(action: string, payload: any = {}): Promise<any> {
      return new Promise((resolve, reject) => {
        if (!this.isNeuroSamaConnected || !this.neuroSamaWs) {
          reject(new Error('Not connected to Neuro Sama WebSocket'))
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
            this.neuroSamaWs?.removeEventListener('message', responseHandler)

            if (response.type === 'response') {
              resolve(response.payload)
            } else {
              reject(new Error(`Request failed: ${JSON.stringify(response)}`))
            }
          }
        }

        // Add event listener for response
        this.neuroSamaWs.addEventListener('message', responseHandler)

        // Send message
        this.neuroSamaWs.send(JSON.stringify(message))

        // Set timeout to reject if no response
        setTimeout(() => {
          this.neuroSamaWs?.removeEventListener('message', responseHandler)
          reject(new Error('Request timeout'))
        }, 10000) // 10 second timeout
      })
    }
  }
})