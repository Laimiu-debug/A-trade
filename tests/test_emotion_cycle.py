import copy
import unittest

from service.emotion_cycle import cycle_timeline
from service.market_service import MarketService
from tests import test_analysis_service as fixture_module
trading_days = fixture_module.trading_days


def daily_rows(count=25, *, premium=2, promotion=.6, height=4, up=20, down=0, high_fail=.2, observed=100):
    rows=[]
    for day in trading_days(count):
        bucket={"eligible":10,"observed":10,"promoted":round(10*promotion),"broken":round(10*(1-promotion)),"missing":0,"rate":promotion}
        rows.append({"date":day,"maxHeight":height,"limitUpCount":up,
            "metrics":{"observedCount":observed,"limitUpCount":up,"maxHeight":height,
                       "premium":{"eligible":10,"observed":10,"mean":premium},
                       "promotion":{"all":bucket},"highBoardPromotion":dict(bucket,rate=1-high_fail),
                       "risk":{"limitDownCount":down,"declineRatio":.2}}})
    return rows


class EmotionRulesTests(unittest.TestCase):
    def test_two_day_confirmation_and_immediate_negative_feedback(self):
        rows=daily_rows()
        result=cycle_timeline(rows,[100]*len(rows))
        self.assertEqual(result[10]['stage'],'unknown')
        self.assertEqual(result[11]['stage'],'advance')
        rows[-1]['metrics']['premium']['mean']=-3
        rows[-1]['metrics']['highBoardPromotion']['rate']=.2
        result=cycle_timeline(rows,[100]*len(rows))
        self.assertEqual(result[-1]['stage'],'decline')
        self.assertEqual(result[-1]['evidence']['highFailure'],.8)

    def test_missing_cohorts_and_bad_coverage_cannot_confirm_a_phase(self):
        rows=daily_rows()
        for row in rows:
            row['metrics']['premium']['observed']=1
            row['metrics']['promotion']['all']['observed']=1
            row['metrics']['highBoardPromotion']['observed']=1
        result=cycle_timeline(rows,[100]*len(rows))
        self.assertEqual(result[-1]['stage'],'unknown')
        result=cycle_timeline(daily_rows(observed=60),[100]*25)
        self.assertEqual(result[-1]['stage'],'unknown')

    def test_appending_future_observations_does_not_rewrite_history(self):
        rows=daily_rows()
        prefix=cycle_timeline(copy.deepcopy(rows[:20]),[100]*20)
        self.assertEqual(prefix,cycle_timeline(rows,[100]*25)[:20])

    def test_phase_expires_instead_of_perpetually_carrying_an_old_label(self):
        rows=daily_rows(count=35)
        for row in rows[20:]:
            row['metrics']['premium']['observed']=1
            row['metrics']['promotion']['all']['observed']=1
            row['metrics']['highBoardPromotion']['observed']=1
        self.assertEqual(cycle_timeline(rows,[100]*35)[-1]['stage'],'unknown')


class EmotionServiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.AnalysisTests('test_trend_conditions_real_returns_and_index_exclusion')
        self.fixture.setUp()
        self.owner=self.fixture.owner

    def tearDown(self):
        self.fixture.tearDown()

    def seed(self):
        days=trading_days(110)
        for i in range(25):
            values=[10]*107
            if i==0: values += [11,12.1,13.31]
            elif i in (1,2): values += [10,10,11]
            else: values += [10,10,10.1]
            self.fixture.write('sh'+str(600001+i), values)
        self.fixture.scan()
        return days

    def analyse(self,**payload):
        return self.owner.dispatch('market.analysis.sentiment',payload)

    def test_empty_directory_and_payload_validation(self):
        result=self.analyse()
        self.assertIsNone(result['current'])
        self.assertEqual(result['timeline'],[])
        for payload in ({'historyDays':True},{'themeId':[]},{'asOf':'2999-01-01'},{'injected':1}):
            with self.assertRaises(ValueError): self.analyse(**payload)

    def test_actual_bars_produce_leaders_and_dated_theme_followers(self):
        days=self.seed()
        result=self.analyse(historyDays=60)
        self.assertEqual(result['leaders'][0]['symbol'],'sh600001')
        self.assertEqual(result['leaders'][0]['boardHeight'],3)
        self.assertEqual(result['current']['scope'],'sample')
        self.assertEqual(result['themes'],[])
        self.assertEqual(len(result['lowCandidates']),2)
        theme=self.owner.dispatch('market.emotion.themes.save',{'name':'测试题材','symbols':['sh600001','sh600002','sh600003'],'effectiveFrom':days[100]})
        themed=self.analyse(themeId=theme['id'])
        self.assertEqual(len(themed['selectedTheme']['catchups']),2)
        self.assertEqual(len(themed['selectedTheme']['followers']),2)
        earlier=self.analyse(asOf=days[99])
        self.assertEqual(earlier['themes'],[])
        self.assertIsNone(earlier['selectedTheme'])
        with self.assertRaises(ValueError):
            self.owner.dispatch('market.emotion.themes.save',{**theme,'symbols':['sh600001','sh600002']})
        reopened=MarketService(self.fixture.base/'state')
        try: self.assertEqual(reopened.dispatch('market.emotion.themes.list')['items'][0]['id'],theme['id'])
        finally: reopened.close()

    def test_future_bars_and_window_changes_do_not_relabel_earlier_observations(self):
        days=self.seed()
        before=self.analyse(asOf=days[100],historyDays=60)
        self.fixture.write('sh600001',[10]*107+[11,12.1,13.31]+[14.64,16.10,17.71])
        after=self.analyse(asOf=days[100],historyDays=120,refresh=True)
        self.assertEqual(before['current'],after['current'])
        self.assertEqual(before['timeline'],after['timeline'][-60:])
        self.assertTrue(all(x['date']<=days[100] for x in after['timeline']))

    def test_today_sector_membership_cannot_be_backdated_or_accept_unknown_stocks(self):
        days=self.seed()
        with self.assertRaisesRegex(ValueError,'不能回填'):
            self.owner.dispatch('market.emotion.themes.save',{'name':'今日成分','symbols':['sh600001','sh600002'],'source':'tdx_snapshot','effectiveFrom':days[-1]})
        with self.assertRaisesRegex(ValueError,'不在当前'):
            self.owner.dispatch('market.emotion.themes.save',{'name':'坏归属','symbols':['sh600001','sh699999'],'effectiveFrom':days[-1]})


if __name__=='__main__': unittest.main()
