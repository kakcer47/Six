#!/usr/bin/env node
/**
 * Posts Server - Optimized for Render Production
 * ==============================================
 * 
 * Оптимизированный сервер для событий:
 * - Минимальное использование RAM (< 100MB)
 * - Stateless архитектура
 * - Без кеша (только БД)
 * - Быстрые простые запросы
 * - Интеграция с Accounts сервером
 */

const express = require('express')
const cors = require('cors')
const { Pool } = require('pg')
const WebSocket = require('ws')
const http = require('http')

class PostsServer {
  constructor() {
    this.serverId = process.env.SERVER_ID || `posts_${Date.now()}`
    this.port = process.env.PORT || 3000

    // Минимальный статистический кеш (только счетчики)
    this.stats = {
      totalEvents: 0,
      totalRequests: 0,
      startTime: Date.now()
    }

    // WebSocket клиенты для real-time обновлений
    this.wsClients = new Set()

    this.initializeServices()
  }

  async initializeServices() {
    // Express + WebSocket
    this.app = express()
    this.server = http.createServer(this.app)
    this.wss = new WebSocket.Server({ server: this.server })

    this.setupMiddleware()

    // База данных (основное хранилище)
    if (process.env.DATABASE_URL && process.env.DATABASE_URL !== 'disabled') {
      this.db = new Pool({
        connectionString: process.env.DATABASE_URL,
        ssl: process.env.NODE_ENV === 'production',
        max: 5, // Минимум подключений
        idleTimeoutMillis: 30000,
        connectionTimeoutMillis: 10000
      })
      await this.initializeDatabase()
      await this.updateStats()
    } else {
      console.log('📝 Database disabled - using memory store')
      this.memoryEvents = new Map()
      this.db = null
    }

    this.setupRoutes()
    this.setupWebSocket()
    this.startCleanupTasks()

    this.server.listen(this.port, () => {
      console.log(`📰 Posts Server [${this.serverId}] running on port ${this.port}`)
      console.log(`💾 Database: ${this.db ? 'PostgreSQL' : 'Memory'}`)
      console.log(`📊 Total events: ${this.stats.totalEvents}`)
      console.log(`🧠 Memory usage: ${Math.round(process.memoryUsage().heapUsed / 1024 / 1024)}MB`)
    })
  }

  setupMiddleware() {
    this.app.use(cors())
    this.app.use(express.json({ limit: '2mb' }))

    // Request logging & stats
    this.app.use((req, res, next) => {
      this.stats.totalRequests++
      console.log(`📝 ${req.method} ${req.path} - ${req.ip}`)
      next()
    })

    // Health check
    this.app.get('/health', (req, res) => {
      res.json({
        serverId: this.serverId,
        service: 'posts',
        totalEvents: this.stats.totalEvents,
        totalRequests: this.stats.totalRequests,
        wsClients: this.wsClients.size,
        memoryUsage: Math.round(process.memoryUsage().heapUsed / 1024 / 1024),
        uptime: Math.round((Date.now() - this.stats.startTime) / 1000),
        status: 'healthy'
      })
    })
  }

  setupRoutes() {
    // === ОСНОВНЫЕ API ===

    // Получить ленту событий (основной запрос фронтенда)
    this.app.get('/api/feed', async (req, res) => {
      try {
        const {
          page = 1,
          limit = 50,
          search,
          city,
          category,
          gender,     
          ageGroup,    
          dateFrom,    
          dateTo,     
          authorId,
          since
        } = req.query

        console.log(`📡 Feed request: page=${page}, limit=${limit}, search="${search || ''}"`)

        const events = await this.getEvents({
          page: parseInt(page),
          limit: Math.min(parseInt(limit), 100),
          search, city, category, gender, ageGroup, dateFrom, dateTo, authorId, since
        })

        res.json({
          posts: events,
          hasMore: events.length === parseInt(limit),
          total: this.stats.totalEvents,
          serverId: this.serverId,
          timestamp: Date.now()
        })

        console.log(`📡 Returned ${events.length} events`)

      } catch (error) {
        console.error('Feed error:', error)
        res.status(500).json({ error: 'Failed to fetch events' })
      }
    })

    // Создать событие
    this.app.post('/api/events', async (req, res) => {
      try {
        const eventData = req.body

        if (!eventData.title || !eventData.description || !eventData.authorId) {
          return res.status(400).json({ error: 'Missing required fields' })
        }

        const event = await this.createEvent(eventData)

        this.broadcastToClients('EVENT_CREATED', event)

        res.json(event)
        console.log(`✅ Event created: ${event.title} (${event.id})`)

      } catch (error) {
        console.error('Create event error:', error)
        res.status(500).json({ error: 'Failed to create event' })
      }
    })

    this.app.put('/api/events/:id', async (req, res) => {
      try {
        const { id } = req.params
        const updates = req.body

        const event = await this.updateEvent(id, updates)

        if (!event) {
          return res.status(404).json({ error: 'Event not found' })
        }

        // Уведомляем клиентов
        this.broadcastToClients('EVENT_UPDATED', event)

        res.json(event)
        console.log(`📝 Event updated: ${id}`)

      } catch (error) {
        console.error('Update event error:', error)
        res.status(500).json({ error: 'Failed to update event' })
      }
    })

    // Удалить событие
    this.app.delete('/api/events/:id', async (req, res) => {
      try {
        const { id } = req.params

        await this.deleteEvent(id)

        // Уведомляем клиентов
        this.broadcastToClients('EVENT_DELETED', { id })

        res.json({ success: true })
        console.log(`🗑️ Event deleted: ${id}`)

      } catch (error) {
        console.error('Delete event error:', error)
        res.status(500).json({ error: 'Failed to delete event' })
      }
    })

    // Получить конкретное событие
    this.app.get('/api/events/:id', async (req, res) => {
      try {
        const { id } = req.params
        const event = await this.getEventById(id)

        if (!event) {
          return res.status(404).json({ error: 'Event not found' })
        }

        res.json(event)

      } catch (error) {
        console.error('Get event error:', error)
        res.status(500).json({ error: 'Failed to get event' })
      }
    })

    // === BATCH ОПЕРАЦИИ ===

    // Batch обновление лайков (от Accounts сервера)
    this.app.post('/api/events/batch/likes', async (req, res) => {
      try {
        const { updates } = req.body // [{ eventId, increment }]

        if (!Array.isArray(updates)) {
          return res.status(400).json({ error: 'Updates must be an array' })
        }

        const results = await this.batchUpdateLikes(updates)

        res.json({
          success: true,
          updated: results.length,
          results
        })

        console.log(`📊 Batch updated ${results.length} likes`)

      } catch (error) {
        console.error('Batch likes error:', error)
        res.status(500).json({ error: 'Failed to batch update likes' })
      }
    })

    // === СТАТИСТИКА ===

    // Статистика событий
    this.app.get('/api/stats', async (req, res) => {
      try {
        const stats = await this.getDetailedStats()
        res.json(stats)
      } catch (error) {
        console.error('Stats error:', error)
        res.status(500).json({ error: 'Failed to get stats' })
      }
    })

    // Топ города
    this.app.get('/api/stats/cities', async (req, res) => {
      try {
        const cities = await this.getTopCities()
        res.json(cities)
      } catch (error) {
        console.error('Cities stats error:', error)
        res.status(500).json({ error: 'Failed to get cities stats' })
      }
    })

    // Топ категории
    this.app.get('/api/stats/categories', async (req, res) => {
      try {
        const categories = await this.getTopCategories()
        res.json(categories)
      } catch (error) {
        console.error('Categories stats error:', error)
        res.status(500).json({ error: 'Failed to get categories stats' })
      }
    })
  }

  setupWebSocket() {
    this.wss.on('connection', (ws) => {
      this.wsClients.add(ws)
      console.log(`📡 WebSocket client connected (${this.wsClients.size} total)`)

      // Отправляем статистику при подключении
      ws.send(JSON.stringify({
        type: 'STATS',
        data: {
          totalEvents: this.stats.totalEvents,
          connectedClients: this.wsClients.size
        }
      }))

      ws.on('error', (error) => {
        console.error('WebSocket error:', error)
        this.wsClients.delete(ws)
      })

      ws.on('message', (message) => {
        try {
          const data = JSON.parse(message)
          console.log('📡 Received WebSocket message:', data.type)

          // Ретранслируем сообщения всем остальным клиентам
          this.broadcastToClients(data.type, data.data, ws)
        } catch (error) {
          console.error('WebSocket message parse error:', error)
        }
      })
    })
  }

  broadcastToClients(type, data, excludeClient = null) {
    if (this.wsClients.size === 0) return

    const message = JSON.stringify({ type, data, timestamp: Date.now() })
    let sent = 0

    this.wsClients.forEach(client => {
      if (client !== excludeClient && client.readyState === WebSocket.OPEN) {
        try {
          client.send(message)
          sent++
        } catch (error) {
          console.error('WebSocket send error:', error)
          this.wsClients.delete(client)
        }
      } else if (client.readyState !== WebSocket.OPEN) {
        this.wsClients.delete(client)
      }
    })

    console.log(`📡 Broadcasted ${type} to ${sent} clients`)
  }

  // === БАЗА ДАННЫХ ===
  async initializeDatabase() {
    try {
      await this.db.query(`
      CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        author_id TEXT NOT NULL,
        author_name TEXT NOT NULL,
        author_avatar TEXT DEFAULT '',
        author_username TEXT DEFAULT '',
        author_telegram_id BIGINT,
        city TEXT DEFAULT '',
        category TEXT DEFAULT '',
        gender TEXT DEFAULT '',
        age_group TEXT DEFAULT '',
        date_from DATE,
        date_to DATE,
        likes INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW(),
        status TEXT DEFAULT 'active'
      )
    `)

      // Добавляем новые колонки для существующих баз
      await this.db.query(`
      ALTER TABLE events 
      ADD COLUMN IF NOT EXISTS gender TEXT DEFAULT '',
      ADD COLUMN IF NOT EXISTS age_group TEXT DEFAULT '',
      ADD COLUMN IF NOT EXISTS date_from DATE,
      ADD COLUMN IF NOT EXISTS date_to DATE,
      ADD COLUMN IF NOT EXISTS author_avatar TEXT DEFAULT '',
      ADD COLUMN IF NOT EXISTS author_username TEXT DEFAULT '',
      ADD COLUMN IF NOT EXISTS author_telegram_id BIGINT
    `)

      // Индексы
      await this.db.query(`
      CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
      CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
      CREATE INDEX IF NOT EXISTS idx_events_author ON events(author_id);
      CREATE INDEX IF NOT EXISTS idx_events_city ON events(city);
      CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
      CREATE INDEX IF NOT EXISTS idx_events_gender ON events(gender);
      CREATE INDEX IF NOT EXISTS idx_events_age_group ON events(age_group);
      CREATE INDEX IF NOT EXISTS idx_events_likes ON events(likes DESC);
    `)

      console.log('✅ Posts database initialized')
    } catch (error) {
      console.error('❌ Database initialization failed:', error)
    }
  }

  async updateStats() {
    if (!this.db) {
      this.stats.totalEvents = this.memoryEvents?.size || 0
      return
    }

    try {
      const result = await this.db.query(`
        SELECT COUNT(*) as total 
        FROM events 
        WHERE status = 'active'
      `)
      this.stats.totalEvents = parseInt(result.rows[0]?.total || 0)
    } catch (error) {
      console.error('Stats update error:', error)
    }
  }

  // === CRUD ОПЕРАЦИИ ===

  async createEvent(eventData) {
    const event = {
      id: `${this.serverId}_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      title: eventData.title.trim(),
      description: eventData.description.trim(),
      authorId: eventData.authorId,
      authorName: eventData.author?.fullName || eventData.authorName || 'Anonymous',
      authorAvatar: eventData.author?.avatar || '',
      authorUsername: eventData.author?.username || '',
      authorTelegramId: eventData.author?.telegramId || null,
      city: eventData.city?.trim() || '',
      category: eventData.category || '',
      gender: eventData.gender || '',
      ageGroup: eventData.ageGroup || '',
      dateFrom: eventData.dateFrom || null,
      dateTo: eventData.dateTo || null,
      likes: 0,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      status: 'active'
    }

    if (this.db) {
      await this.db.query(`
  INSERT INTO events (id, title, description, author_id, author_name, author_avatar, author_username, author_telegram_id, city, category, gender, age_group, date_from, date_to, likes, created_at, updated_at, status)
  VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
`, [
        event.id, event.title, event.description, event.authorId,
        event.authorName, event.author?.avatar, event.author?.username, event.author?.telegramId,
        event.city, event.category, event.gender, event.ageGroup, event.dateFrom, event.dateTo,
        event.likes, event.createdAt, event.updatedAt, event.status
      ])
    } else {
      this.memoryEvents.set(event.id, event)
    }

    this.stats.totalEvents++
    return this.formatEventForAPI(event)
  }

  async getEvents({ page, limit, search, city, category, authorId, since }) {
    let query = `
      SELECT * FROM events 
      WHERE status = 'active'
    `
    const params = []
    let paramIndex = 1

    // Фильтры
    if (search) {
      query += ` AND (title ILIKE $${paramIndex} OR description ILIKE $${paramIndex})`
      params.push(`%${search}%`)
      paramIndex++
    }

    if (city) {
      query += ` AND city = $${paramIndex}`
      params.push(city)
      paramIndex++
    }

    if (category) {
      query += ` AND category = $${paramIndex}`
      params.push(category)
      paramIndex++
    }

    if (authorId) {
      query += ` AND author_id = $${paramIndex}`
      params.push(authorId)
      paramIndex++
    }

    if (gender) {
      query += ` AND gender = $${paramIndex}`
      params.push(gender)
      paramIndex++
    }

    if (ageGroup) {
      query += ` AND age_group = $${paramIndex}`
      params.push(ageGroup)
      paramIndex++
    }

    if (dateFrom) {
      query += ` AND (date_to IS NULL OR date_to >= $${paramIndex})`
      params.push(dateFrom)
      paramIndex++
    }

    if (dateTo) {
      query += ` AND (date_from IS NULL OR date_from <= $${paramIndex})`
      params.push(dateTo)
      paramIndex++
    }

    if (since) {
      query += ` AND updated_at > $${paramIndex}`
      params.push(new Date(parseInt(since)))
      paramIndex++
    }

    // Сортировка и пагинация
    query += ` ORDER BY created_at DESC`
    query += ` LIMIT $${paramIndex} OFFSET $${paramIndex + 1}`
    params.push(limit, (page - 1) * limit)

    if (this.db) {
      const result = await this.db.query(query, params)
      return result.rows.map(row => this.formatEventFromDB(row))
    } else {
      // Memory fallback
      let events = Array.from(this.memoryEvents.values())
        .filter(event => event.status === 'active')

      if (search) {
        const searchLower = search.toLowerCase()
        events = events.filter(event =>
          event.title.toLowerCase().includes(searchLower) ||
          event.description.toLowerCase().includes(searchLower)
        )
      }

      if (city) events = events.filter(event => event.city === city)
      if (category) events = events.filter(event => event.category === category)
      if (authorId) events = events.filter(event => event.authorId === authorId)

      events.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))

      const offset = (page - 1) * limit
      return events.slice(offset, offset + limit).map(event => this.formatEventForAPI(event))
    }
  }

  async getEventById(eventId) {
    if (this.db) {
      const result = await this.db.query('SELECT * FROM events WHERE id = $1', [eventId])
      return result.rows[0] ? this.formatEventFromDB(result.rows[0]) : null
    } else {
      const event = this.memoryEvents.get(eventId)
      return event ? this.formatEventForAPI(event) : null
    }
  }

  async updateEvent(eventId, updates) {
    const allowedUpdates = ['title', 'description', 'city', 'category', 'likes']
    const updateData = {}

    // Фильтруем разрешенные поля
    for (const [key, value] of Object.entries(updates)) {
      if (allowedUpdates.includes(key)) {
        updateData[key] = value
      }
    }

    if (Object.keys(updateData).length === 0) {
      throw new Error('No valid fields to update')
    }

    updateData.updatedAt = new Date().toISOString()

    if (this.db) {
      const setClause = Object.keys(updateData)
        .map((key, index) => `${this.camelToSnake(key)} = $${index + 2}`)
        .join(', ')

      const query = `
        UPDATE events 
        SET ${setClause}
        WHERE id = $1 AND status = 'active'
        RETURNING *
      `

      const params = [eventId, ...Object.values(updateData)]
      const result = await this.db.query(query, params)

      return result.rows[0] ? this.formatEventFromDB(result.rows[0]) : null
    } else {
      const event = this.memoryEvents.get(eventId)
      if (!event || event.status !== 'active') return null

      Object.assign(event, updateData)
      return this.formatEventForAPI(event)
    }
  }

  async deleteEvent(eventId) {
    if (this.db) {
      await this.db.query('UPDATE events SET status = $1 WHERE id = $2', ['deleted', eventId])
    } else {
      const event = this.memoryEvents.get(eventId)
      if (event) {
        event.status = 'deleted'
      }
    }

    this.stats.totalEvents = Math.max(0, this.stats.totalEvents - 1)
  }

  async batchUpdateLikes(updates) {
    if (!this.db) {
      // Memory fallback
      return updates.map(update => {
        const event = this.memoryEvents.get(update.eventId)
        if (event) {
          event.likes = Math.max(0, (event.likes || 0) + update.increment)
          return { eventId: update.eventId, newLikes: event.likes }
        }
        return null
      }).filter(Boolean)
    }

    const results = []

    for (const update of updates) {
      try {
        const result = await this.db.query(`
          UPDATE events 
          SET likes = GREATEST(0, likes + $1), updated_at = NOW()
          WHERE id = $2 AND status = 'active'
          RETURNING id, likes
        `, [update.increment, update.eventId])

        if (result.rows[0]) {
          results.push({
            eventId: result.rows[0].id,
            newLikes: result.rows[0].likes
          })
        }
      } catch (error) {
        console.error(`Batch like update failed for ${update.eventId}:`, error)
      }
    }

    return results
  }

  // === СТАТИСТИКА ===

  async getDetailedStats() {
    if (!this.db) {
      return {
        totalEvents: this.memoryEvents?.size || 0,
        totalRequests: this.stats.totalRequests,
        wsClients: this.wsClients.size,
        uptime: Math.round((Date.now() - this.stats.startTime) / 1000),
        memoryUsage: Math.round(process.memoryUsage().heapUsed / 1024 / 1024)
      }
    }

    const [total, recent, popular] = await Promise.all([
      this.db.query('SELECT COUNT(*) as count FROM events WHERE status = $1', ['active']),
      this.db.query('SELECT COUNT(*) as count FROM events WHERE status = $1 AND created_at > NOW() - INTERVAL \'24 hours\'', ['active']),
      this.db.query('SELECT COUNT(*) as count FROM events WHERE status = $1 AND likes > 0', ['active'])
    ])

    return {
      totalEvents: parseInt(total.rows[0]?.count || 0),
      recentEvents: parseInt(recent.rows[0]?.count || 0),
      popularEvents: parseInt(popular.rows[0]?.count || 0),
      totalRequests: this.stats.totalRequests,
      wsClients: this.wsClients.size,
      uptime: Math.round((Date.now() - this.stats.startTime) / 1000),
      memoryUsage: Math.round(process.memoryUsage().heapUsed / 1024 / 1024)
    }
  }

  async getTopCities() {
    if (!this.db) return []

    const result = await this.db.query(`
      SELECT city, COUNT(*) as count 
      FROM events 
      WHERE status = 'active' AND city != '' 
      GROUP BY city 
      ORDER BY count DESC 
      LIMIT 10
    `)

    return result.rows
  }

  async getTopCategories() {
    if (!this.db) return []

    const result = await this.db.query(`
      SELECT category, COUNT(*) as count 
      FROM events 
      WHERE status = 'active' AND category != '' 
      GROUP BY category 
      ORDER BY count DESC 
      LIMIT 10
    `)

    return result.rows
  }

  // === УТИЛИТЫ ===

  formatEventFromDB(row) {
    return {
      id: row.id,
      title: row.title,
      description: row.description,
      authorId: row.author_id,
      author: {
        fullName: row.author_name,
        avatar: row.author_avatar,
        username: row.author_username,
        telegramId: row.author_telegram_id
      },
      city: row.city || '',
      category: row.category || '',
      gender: row.gender || '',
      ageGroup: row.age_group || '',
      dateFrom: row.date_from,
      dateTo: row.date_to,
      likes: row.likes || 0,
      createdAt: row.created_at.toISOString(),
      updatedAt: row.updated_at.toISOString(),
      status: row.status
    }
  }

  formatEventForAPI(event) {
    return {
      id: event.id,
      title: event.title,
      description: event.description,
      authorId: event.authorId,
      author: {
        fullName: event.authorName,
        avatar: event.authorAvatar,
        username: event.authorUsername,
        telegramId: event.authorTelegramId
      },
      city: event.city || '',
      category: event.category || '',
      gender: event.gender || '',
      ageGroup: event.ageGroup || '',
      dateFrom: event.dateFrom,
      dateTo: event.dateTo,
      likes: event.likes || 0,
      createdAt: event.createdAt,
      updatedAt: event.updatedAt,
      status: event.status
    }
  }

  camelToSnake(str) {
    return str.replace(/[A-Z]/g, letter => `_${letter.toLowerCase()}`)
  }

  // === ОЧИСТКА ===

  startCleanupTasks() {
    // Обновляем статистику каждые 5 минут
    setInterval(async () => {
      await this.updateStats()

      // Принудительная сборка мусора при высоком потреблении
      const memUsage = process.memoryUsage().heapUsed
      if (memUsage > 300 * 1024 * 1024) { // 300MB
        console.log('🚨 High memory usage, forcing garbage collection')
        global.gc && global.gc()
      }

      console.log(`📊 Stats: ${this.stats.totalEvents} events, ${this.wsClients.size} WS clients, ${Math.round(memUsage / 1024 / 1024)}MB RAM`)
    }, 5 * 60 * 1000)

    // Очистка неактивных WebSocket соединений
    setInterval(() => {
      this.wsClients.forEach(client => {
        if (client.readyState !== WebSocket.OPEN) {
          this.wsClients.delete(client)
        }
      })
    }, 30 * 1000)

    // Очистка старых событий (опционально)
    if (this.db && process.env.ENABLE_CLEANUP === 'true') {
      setInterval(async () => {
        try {
          const result = await this.db.query(`
            UPDATE events 
            SET status = 'archived'
            WHERE status = 'active' 
            AND created_at < NOW() - INTERVAL '30 days'
            AND likes < 1
          `)

          if (result.rowCount > 0) {
            console.log(`🗂️ Archived ${result.rowCount} old events`)
            await this.updateStats()
          }
        } catch (error) {
          console.error('Cleanup error:', error)
        }
      }, 60 * 60 * 1000) // Каждый час
    }
  }
}

// === ЗАПУСК СЕРВЕРА ===

const server = new PostsServer()

// Graceful shutdown
process.on('SIGTERM', async () => {
  console.log('🛑 Posts server graceful shutdown...')
  server.wss.close()
  if (server.db) await server.db.end()
  process.exit(0)
})

process.on('SIGINT', async () => {
  console.log('🛑 Posts server interrupted, shutting down...')
  server.wss.close()
  if (server.db) await server.db.end()
  process.exit(0)
})

// Обработка uncaught exceptions
process.on('uncaughtException', (error) => {
  console.error('💥 Uncaught Exception:', error)
  process.exit(1)
})

process.on('unhandledRejection', (reason, promise) => {
  console.error('💥 Unhandled Rejection at:', promise, 'reason:', reason)
  process.exit(1)
})
