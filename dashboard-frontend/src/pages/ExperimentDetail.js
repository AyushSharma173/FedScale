import React, { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import { useParams, useNavigate } from 'react-router-dom'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'
import '../App.css'  // or ExperimentDetail.css if you split styles

export default function ExperimentDetail() {
  const { id } = useParams()
  const nav = useNavigate()

  const [expt, setExpt] = useState(null)
  const [status, setStatus] = useState(null)
  const [metrics, setMetrics] = useState([])
  const [curves, setCurves] = useState({})
  const [overview, setOverview] = useState([])
  const [selClient, setSelClient] = useState(null)
  const [selRound, setSelRound] = useState(null)
  const esRef = useRef(null)

  // fetch experiment metadata
  useEffect(() => {
    axios.get('http://localhost:8000/experiments')
      .then(r => setExpt(r.data.find(x => x.id === id)))
      .catch(console.error)
  }, [id])

  // SSE subscription
  useEffect(() => {
    if (esRef.current) esRef.current.close()
    setStatus(null)
    setMetrics([])
    setCurves({})
    setOverview([])
    setSelClient(null)
    setSelRound(null)

    const es = new EventSource(`http://localhost:8000/experiments/${id}/stream`)
    esRef.current = es

    es.onmessage = e => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'status') {
        setStatus(msg)
      } else {
        // metrics event
        setMetrics(m => [...m, msg])
        msg.clients.forEach(c => {
          setCurves(p => ({
            ...p,
            [c.id]: [
              ...(p[c.id] || []),
              { round: msg.round, loss_curve: c.loss_curve }
            ]
          }))
        })
        const avgL = msg.clients.reduce((s, c) => s + c.client_eval_local_acc, 0) / msg.clients.length
        const avgG = msg.clients.reduce((s, c) => s + c.client_eval_global_acc, 0) / msg.clients.length
        const avgA = msg.clients.reduce((s, c) => s + c.client_alpha, 0) / msg.clients.length
        setOverview(o => [...o, { round: msg.round, avgL, avgG, avgA }])
      }
    }

    return () => es.close()
  }, [id])

  if (!expt) return <p>Loading experiment...</p>

  // find latest client metrics & selected curve
  const latest = [...metrics].reverse().find(m => m.clients.some(c => c.id == selClient))
  const selClientMetrics = latest?.clients.find(c => c.id == selClient)
  const selClientCurve = curves[selClient]?.find(r =>
    r.round === (selRound ?? curves[selClient].slice(-1)[0].round)
  )

  return (
    <div className="dashboard">
      <button onClick={() => nav(-1)}>← Back</button>
      <header><h1>Experiment: {expt.name}</h1></header>

      <div className="status-banner">
        <h2>Round {status?.round} — {status?.running ? 'Running' : 'Finished'}</h2>
        <p>Clock: {status?.virtual_clock.toFixed(1)}s</p>
        <p>Sampled: {status?.sampled_clients.join(', ')}</p>
      </div>

      <section className="global-curve">
        <h3>Global Loss vs Round</h3>
        {metrics.length > 0 ? (
        <LineChart
          width={700}
          height={300}
          data={metrics.map(m => ({ round: m.round, top1: m.top1, top5: m.top5 }))}
        >
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="round" allowDecimals={false} />
          <YAxis />
          <Tooltip
            formatter={v => v.toFixed(3)}
            labelFormatter={l => `Round ${l}`}
          />
          <Legend verticalAlign="top" height={36} />
          <Line
            dataKey="top1"
            name="Top-1 Loss"
            dot={{ r: 4 }}
            stroke="#ff4757"
            strokeWidth={2}
          />
          <Line
            dataKey="top5"
            name="Top-5 Loss"
            dot={{ r: 4 }}
            stroke="#1e90ff"
            strokeWidth={2}
          />
        </LineChart> ) : (
    <p>Waiting for first completed round... (current: Round {status?.round ?? 0})</p>
  )}
      </section>

      <section className="client-section">
        <h3>Client Details</h3>
        <div className="client-tabs">
          {Object.keys(curves).map(cid => (
            <button
              key={cid}
              className={selClient == cid ? 'active' : ''}
              onClick={() => { setSelClient(cid); setSelRound(null) }}
            >
              Client {cid}
            </button>
          ))}
        </div>

        {selClient && (
          <>
            <label>
              Round:&nbsp;
              <select
                value={selRound ?? curves[selClient].slice(-1)[0].round}
                onChange={e => setSelRound(+e.target.value)}
              >
                {curves[selClient].map(r => (
                  <option key={r.round} value={r.round}>
                    Round {r.round}
                  </option>
                ))}
              </select>
            </label>

            <div className="cards">
              <div className="card">
                <h4>Local Acc</h4>
                <p>{(selClientMetrics.client_eval_local_acc * 100).toFixed(1)}%</p>
              </div>
              <div className="card">
                <h4>Global Acc</h4>
                <p>{(selClientMetrics.client_eval_global_acc * 100).toFixed(1)}%</p>
              </div>
              <div className="card">
                <h4>Mix α</h4>
                <p>{selClientMetrics.client_alpha.toFixed(2)}</p>
              </div>
            </div>

            <LineChart
              width={600}
              height={250}
              data={selClientCurve.loss_curve.map((l, i) => ({ step: i + 1, loss: l }))}
            >
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="step" label={{ value: 'Batch Step', position: 'insideBottom' }} />
              <YAxis label={{ value: 'Loss', angle: -90, position: 'insideLeft' }} />
              <Tooltip formatter={value => value.toFixed(3)} />
              <Line type="monotone" dataKey="loss" dot={{ r: 3 }} stroke="#273c75" />
            </LineChart>
          </>
        )}
      </section>
    </div>
  )
}
