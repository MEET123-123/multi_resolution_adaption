"""Local media metadata and bitmap clock text; no external resource guessing."""
import re
import struct


def mp4_size(data):
    def boxes(start,end,depth=0):
        if depth>8:return None
        offset=start
        while offset+8<=end:
            length,kind=struct.unpack_from('>I4s',data,offset);header=8
            if length==1:
                if offset+16>end:return None
                length=struct.unpack_from('>Q',data,offset+8)[0];header=16
            if length==0:length=end-offset
            if length<header or offset+length>end:return None
            stop=offset+length
            if kind==b'tkhd' and length>=header+8:
                w,h=struct.unpack_from('>II',data,stop-8)
                if w and h:return (w/65536,h/65536)
            if kind in (b'moov',b'trak'):
                result=boxes(offset+header,stop,depth+1)
                if result:return result
            offset=stop
        return None
    return boxes(0,len(data))


def clock_text(runtime,attrs):
    hour=int(runtime.lookup('hour24'));hour12=hour%12 or 12
    values={'HH':f'{hour:02d}','H':str(hour),'hh':f'{hour12:02d}','h':str(hour12),
            'mm':f'{int(runtime.lookup("minute")):02d}','m':str(int(runtime.lookup('minute'))),
            'ss':f'{int(runtime.lookup("second")):02d}','s':str(int(runtime.lookup('second')))}
    return re.sub(r'HH|hh|mm|ss|H|h|m|s',lambda m:values[m[0]],attrs.get('format','HH:mm'))


def datetime_text(runtime,attrs):
    """Common legacy DateTime tokens; system calendar inputs stay explicit."""
    day=int(runtime.lookup('day_of_week'))%7
    long_days=['星期日','星期一','星期二','星期三','星期四','星期五','星期六']
    short_days=['周日','周一','周二','周三','周四','周五','周六']
    month=int(runtime.lookup('month'));date=int(runtime.lookup('date'));year=int(runtime.lookup('year'))
    hour=int(runtime.lookup('hour24'));hour12=hour%12 or 12
    values={'yyyy':str(year),'yy':f'{year%100:02d}','MM':f'{month:02d}','M':str(month),'dd':f'{date:02d}','d':str(date),
            'EEEE':long_days[day],'EEE':short_days[day],'E':short_days[day],
            'HH':f'{hour:02d}','H':str(hour),'hh':f'{hour12:02d}','h':str(hour12),
            'mm':f'{int(runtime.lookup("minute")):02d}','m':str(int(runtime.lookup('minute'))),
            'ss':f'{int(runtime.lookup("second")):02d}','s':str(int(runtime.lookup('second')))}
    pattern='|'.join(sorted(values,key=len,reverse=True))
    return re.sub(pattern,lambda match:values[match[0]],attrs.get('format','MM/dd EEEE'))
